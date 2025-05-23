import os
from flask import Flask, render_template, request, redirect, url_for, flash, send_file, session, jsonify, Response
import boto3
from botocore.exceptions import ClientError
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.urandom(24)

DEFAULT_AWS_CREDS = {
    'region': os.getenv('AWS_REGION'),
    'access_key': os.getenv('AWS_ACCESS_KEY_ID'),
    'secret_key': os.getenv('AWS_SECRET_ACCESS_KEY')
}

def get_s3_client():
    creds = DEFAULT_AWS_CREDS
    return boto3.client(
        's3',
        aws_access_key_id=creds['access_key'],
        aws_secret_access_key=creds['secret_key'],
        region_name=creds['region']
    )

def get_breadcrumbs(prefix):
    if not prefix:
        return []
    parts = prefix.strip('/').split('/')
    breadcrumbs = []
    for i in range(len(parts)):
        breadcrumbs.append({
            'name': parts[i],
            'prefix': '/'.join(parts[:i+1]) + '/'
        })
    return breadcrumbs

@app.route('/')
def list_buckets():
    s3_client = get_s3_client()
    try:
        response = s3_client.list_buckets()
        buckets = response['Buckets']
        return render_template('buckets.html', buckets=buckets)
    except ClientError as e:
        flash(f'Error listing buckets: {str(e)}', 'error')
        return render_template('buckets.html', buckets=[])

@app.route('/bucket/<bucket_name>', methods=['GET', 'POST'])
def index(bucket_name):
    s3_client = get_s3_client()
    prefix = request.args.get('prefix', '')
    files = []
    folders = set()
    try:
        response = s3_client.list_objects_v2(Bucket=bucket_name, Prefix=prefix, Delimiter='/')
        if 'CommonPrefixes' in response:
            for cp in response['CommonPrefixes']:
                folders.add(cp['Prefix'])
        if 'Contents' in response:
            for obj in response['Contents']:
                key = obj['Key']
                if key == prefix or key.endswith('/'):
                    continue
                files.append({
                    'name': key[len(prefix):],
                    'full_key': key,
                    'size': obj['Size'],
                    'last_modified': obj['LastModified']
                })
        breadcrumbs = get_breadcrumbs(prefix)
        return render_template('index.html', files=files, folders=sorted(list(folders)), 
                             selected_bucket=bucket_name, prefix=prefix, breadcrumbs=breadcrumbs)
    except ClientError as e:
        flash(f'Error accessing S3: {str(e)}', 'error')
        return render_template('index.html', files=[], folders=[], 
                             selected_bucket=bucket_name, prefix=prefix, breadcrumbs=[])

@app.route('/upload/<bucket_name>', methods=['POST'])
def upload_file(bucket_name):
    s3_client = get_s3_client()
    prefix = request.form.get('prefix', '')
    if 'file' not in request.files:
        flash('No file selected', 'error')
        return redirect(url_for('index', bucket_name=bucket_name, prefix=prefix))
    file = request.files['file']
    if file.filename == '':
        flash('No file selected', 'error')
        return redirect(url_for('index', bucket_name=bucket_name, prefix=prefix))
    try:
        filename = secure_filename(file.filename)
        s3_key = prefix + filename if prefix else filename
        s3_client.upload_fileobj(file, bucket_name, s3_key)
        flash('File uploaded successfully', 'success')
    except ClientError as e:
        flash(f'Error uploading file: {str(e)}', 'error')
    return redirect(url_for('index', bucket_name=bucket_name, prefix=prefix))

@app.route('/download/<bucket_name>/<path:key>')
def download_file(bucket_name, key):
    s3_client = get_s3_client()
    try:
        response = s3_client.get_object(Bucket=bucket_name, Key=key)
        filename = key.split('/')[-1]
        return send_file(
            response['Body'],
            as_attachment=True,
            download_name=filename
        )
    except ClientError as e:
        flash(f'Error downloading file: {str(e)}', 'error')
        prefix = '/'.join(key.split('/')[:-1]) + ('/' if '/' in key else '')
        return redirect(url_for('index', bucket_name=bucket_name, prefix=prefix))

@app.route('/delete/<bucket_name>/<path:key>')
def delete_file(bucket_name, key):
    s3_client = get_s3_client()
    try:
        s3_client.delete_object(Bucket=bucket_name, Key=key)
        flash('File deleted successfully', 'success')
    except ClientError as e:
        flash(f'Error deleting file: {str(e)}', 'error')
    prefix = '/'.join(key.split('/')[:-1]) + ('/' if '/' in key else '')
    return redirect(url_for('index', bucket_name=bucket_name, prefix=prefix))

@app.route('/create_folder/<bucket_name>', methods=['POST'])
def create_folder(bucket_name):
    s3_client = get_s3_client()
    prefix = request.form.get('prefix', '')
    folder_name = request.form.get('folder_name')
    if not folder_name:
        flash('Folder name is required', 'error')
        return redirect(url_for('index', bucket_name=bucket_name, prefix=prefix))
    if not folder_name.endswith('/'):
        folder_name += '/'
    folder_key = prefix + folder_name if prefix else folder_name
    try:
        s3_client.put_object(Bucket=bucket_name, Key=folder_key)
        flash('Folder created successfully', 'success')
    except ClientError as e:
        flash(f'Error creating folder: {str(e)}', 'error')
    return redirect(url_for('index', bucket_name=bucket_name, prefix=prefix))

@app.route('/api/tree/<bucket_name>')
def api_tree(bucket_name):
    s3_client = get_s3_client()
    prefix = request.args.get('prefix', '')
    tree = []
    try:
        response = s3_client.list_objects_v2(Bucket=bucket_name, Prefix=prefix, Delimiter='/')
        if 'CommonPrefixes' in response:
            for cp in response['CommonPrefixes']:
                tree.append({'type': 'folder', 'name': cp['Prefix'].split('/')[-2], 'prefix': cp['Prefix']})
        if 'Contents' in response:
            for obj in response['Contents']:
                key = obj['Key']
                if key == prefix or key.endswith('/'):
                    continue
                tree.append({'type': 'file', 'name': key.split('/')[-1], 'key': key})
        return jsonify(tree)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/preview/<bucket_name>/<path:key>')
def preview_file(bucket_name, key):
    s3_client = get_s3_client()
    try:
        head = s3_client.head_object(Bucket=bucket_name, Key=key)
        ext = key.split('.')[-1].lower()
        content_type = head.get('ContentType')
        if not content_type or content_type == 'binary/octet-stream':
            if ext == 'mp4':
                content_type = 'video/mp4'
            elif ext == 'webm':
                content_type = 'video/webm'
            elif ext == 'mov':
                content_type = 'video/quicktime'
            else:
                content_type = 'application/octet-stream'
        range_header = request.headers.get('Range', None)
        file_size = head['ContentLength']
        if range_header:
            import re
            match = re.match(r'bytes=(\d+)-(\d*)', range_header)
            if match:
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else file_size - 1
            else:
                start = 0
                end = file_size - 1
            length = end - start + 1
            s3_response = s3_client.get_object(Bucket=bucket_name, Key=key, Range=f'bytes={start}-{end}')
            data = s3_response['Body'].read()
            rv = Response(data, 206, mimetype=content_type, direct_passthrough=True)
            rv.headers.add('Content-Range', f'bytes {start}-{end}/{file_size}')
            rv.headers.add('Accept-Ranges', 'bytes')
            rv.headers.add('Content-Length', str(length))
            return rv
        else:
            s3_response = s3_client.get_object(Bucket=bucket_name, Key=key)
            return Response(s3_response['Body'].read(), mimetype=content_type)
    except Exception as e:
        return f"Error streaming file: {e}", 404

if __name__ == '__main__':
    app.run(debug=True) 