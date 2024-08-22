import os
import json
import base64
from io import BytesIO
import traceback
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from PyPDF2 import PdfReader
from PIL import Image
import fitz  # PyMuPDF
from anthropic import Anthropic
from collections import defaultdict
import boto3
from botocore.exceptions import ClientError

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# S3 configuration
S3_BUCKET = os.environ.get('HERACLES_S3_BUCKET')
S3_PREFIX = os.environ.get('HERACLES_S3_PREFIX', 'heracles')
s3_client = boto3.client('s3')

# Anthropic configuration
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY')
anthropic = Anthropic(api_key=ANTHROPIC_API_KEY)

def upload_file_to_s3(file_name, bucket, object_name=None):
    if object_name is None:
        object_name = os.path.join(S3_PREFIX, os.path.basename(file_name))
    else:
        object_name = os.path.join(S3_PREFIX, object_name)
    try:
        s3_client.upload_file(file_name, bucket, object_name)
    except ClientError as e:
        print(f"Error uploading file to S3: {e}")
        return False
    return True

def download_file_from_s3(bucket, object_name, file_name):
    full_object_name = os.path.join(S3_PREFIX, object_name)
    try:
        s3_client.download_file(bucket, full_object_name, file_name)
    except ClientError as e:
        print(f"Error downloading file from S3: {e}")
        return False
    return True

# ... (rest of the code remains unchanged)

@app.route('/process_pdf', methods=['POST'])
def process_pdf():
    print("Starting process_pdf function")
    try:
        if 'file' not in request.files:
            print("Error: No file part")
            return jsonify({"error": "No file part"}), 400
        
        file = request.files['file']
        if file.filename == '':
            print("Error: No selected file")
            return jsonify({"error": "No selected file"}), 400
        
        if file:
            filename = f'/tmp/{file.filename}'
            file.save(filename)
            print(f"File saved: {filename}")
            
            # Upload the PDF to S3
            s3_pdf_object = f'uploads/{file.filename}'
            upload_file_to_s3(filename, S3_BUCKET, s3_pdf_object)
            
            json_filename = f"{os.path.splitext(file.filename)[0]}_analysis.json"
            json_path = f'/tmp/{json_filename}'
            
            s3_json_object = f'json_results/{json_filename}'
            s3_processed_json_object = f'json_results/{os.path.splitext(file.filename)[0]}_analysis_processed.json'
            
            # Check if processed JSON file already exists in S3
            try:
                s3_client.head_object(Bucket=S3_BUCKET, Key=os.path.join(S3_PREFIX, s3_processed_json_object))
                print(f"Existing processed JSON file found in S3: {s3_processed_json_object}")
                return jsonify({
                    "message": "Processed JSON file already exists",
                    "processed_json_path": f"/get_processed_json/{os.path.basename(s3_processed_json_object)}"
                }), 200
            except ClientError:
                # Processed JSON doesn't exist, continue processing
                pass
            
            # Check if JSON file already exists in S3
            try:
                s3_client.head_object(Bucket=S3_BUCKET, Key=os.path.join(S3_PREFIX, s3_json_object))
                print(f"Existing JSON file found in S3: {s3_json_object}")
                download_file_from_s3(S3_BUCKET, s3_json_object, json_path)
            except ClientError:
                # JSON doesn't exist, process the PDF
                pdf_document = fitz.open(filename)
                print(f"PDF opened: {filename}")
                
                all_pages_analysis = {}
                
                for page_num in range(len(pdf_document)):
                    page = pdf_document[page_num]
                    print(f"Processing page {page_num + 1}")
                    
                    # Convert PDF page to image
                    pix = page.get_pixmap()
                    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                    
                    image_filename = f"{os.path.splitext(file.filename)[0]}_page_{page_num+1}.png"
                    image_path = f'/tmp/{image_filename}'
                    img.save(image_path)
                    print(f"Image saved: {image_path}")
                    
                    # Upload the image to S3
                    s3_image_object = f'images/{image_filename}'
                    upload_file_to_s3(image_path, S3_BUCKET, s3_image_object)
                    
                    # Analyze the image
                    analysis_result = analyze_page_with_claude(image_path)
                    all_pages_analysis[f"page_{page_num + 1}"] = analysis_result
                    print(f"Analysis completed for page {page_num + 1}")
                    
                    # Update the JSON file after each page analysis
                    with open(json_path, 'w') as json_file:
                        json.dump(all_pages_analysis, json_file, indent=2)
                    print(f"Updated JSON file: {json_path}")
                    
                    # Upload the updated JSON to S3
                    upload_file_to_s3(json_path, S3_BUCKET, s3_json_object)
                
                pdf_document.close()
                print("PDF processing and analysis completed")
            
            # Process the JSON results to generate common topics and categorize them
            processed_json_object = process_json_results(json_path)
            
            if processed_json_object:
                return jsonify({
                    "message": "PDF processed, analyzed, and topics categorized successfully",
                    "processed_json_path": f"/get_processed_json/{os.path.basename(processed_json_object)}"
                }), 200
            else:
                return jsonify({
                    "error": "Failed to process JSON results"
                }), 500
    except Exception as e:
        error_message = f"An error occurred while processing the PDF: {str(e)}\n{traceback.format_exc()}"
        print(error_message)
        return jsonify({"error": error_message}), 500

@app.route('/get_processed_json/<filename>', methods=['GET'])
def get_processed_json(filename):
    try:
        s3_object = os.path.join(S3_PREFIX, f'json_results/{filename}')
        file_obj = s3_client.get_object(Bucket=S3_BUCKET, Key=s3_object)
        return send_file(
            BytesIO(file_obj['Body'].read()),
            mimetype='application/json',
            as_attachment=True,
            download_name=filename
        )
    except ClientError as e:
        return jsonify({"error": "File not found"}), 404

@app.route('/get_pdf_page/<filename>/<int:page_number>', methods=['GET'])
def get_pdf_page(filename, page_number):
    try:
        s3_pdf_object = os.path.join(S3_PREFIX, f'uploads/{filename}')
        local_pdf_path = f'/tmp/{filename}'
        download_file_from_s3(S3_BUCKET, s3_pdf_object, local_pdf_path)

        pdf_document = fitz.open(local_pdf_path)
        if page_number < 1 or page_number > len(pdf_document):
            return jsonify({"error": "Invalid page number"}), 400

        page = pdf_document[page_number - 1]
        pix = page.get_pixmap()
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

        img_io = BytesIO()
        img.save(img_io, 'PNG')
        img_io.seek(0)

        return send_file(img_io, mimetype='image/png')
    except ClientError as e:
        return jsonify({"error": "PDF file not found"}), 404
    except Exception as e:
        error_message = f"Error rendering PDF page: {str(e)}\n{traceback.format_exc()}"
        print(error_message)
        return jsonify({"error": error_message}), 500
    finally:
        if 'pdf_document' in locals():
            pdf_document.close()

if __name__ == '__main__':
    app.run(debug=True)