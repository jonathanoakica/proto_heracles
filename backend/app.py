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
s3_client = boto3.client('s3')

# Anthropic configuration
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY')
anthropic = Anthropic(api_key=ANTHROPIC_API_KEY)

def upload_file_to_s3(file_name, bucket, object_name=None):
    if object_name is None:
        object_name = file_name
    try:
        s3_client.upload_file(file_name, bucket, object_name)
    except ClientError as e:
        print(f"Error uploading file to S3: {e}")
        return False
    return True

def download_file_from_s3(bucket, object_name, file_name):
    try:
        s3_client.download_file(bucket, object_name, file_name)
    except ClientError as e:
        print(f"Error downloading file from S3: {e}")
        return False
    return True

def encode_image_to_base64(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

def analyze_page_with_claude(image_path):
    base64_image = encode_image_to_base64(image_path)
    
    results = []
    retry_count = 0
    max_retries = 10

    check_for_keys = ["topics", "tables", "summary"]

    while retry_count < max_retries:
        try:
            response = anthropic.messages.create(
                model="claude-3-sonnet-20240229",
                max_tokens=4096,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": base64_image
                                }
                            },
                            {
                                "type": "text",
                                "text": """
                                {
                                  "role": "You are an expert page summarizer and data extractor.",
                                  "task": "Analyze the provided image of a page and extract key information.",
                                  "output_format": {
                                    "type": "JSON",
                                    "structure": {
                                      "topics": ["Array of main topics identified on the page"],
                                      "tables": {
                                        "table_name": {
                                          "description": "Dictionary representation of each table",
                                          "note": "Maintain the original table structure for easy conversion to a dataframe"
                                        }
                                      },
                                      "summary": "A concise summary of the page content"
                                    }
                                  },
                                  "instructions": [
                                    "Identify and list the main topics present on the page",
                                    "Extract any tables, preserving their structure",
                                    "Represent tables as nested dictionaries within the 'tables' object",
                                    "Use descriptive names for table keys based on table content or context",
                                    "Provide a concise summary of the page content",
                                    "Ensure the output is valid JSON and nothing else"
                                  ]
                                }

                                Human: Please analyze the following image:
                                """
                            }
                        ]
                    }
                ]
            )

            response_content = response.content[0].text
            response_json = json.loads(response_content)

            if all(key in response_json for key in check_for_keys):
                results.append(response_json)
                break  # Exit the loop if all keys are present
            else:
                raise KeyError("Not all required keys are present in the response")
        except Exception as e:
            print(f"Error occurred: {e}")
            retry_count += 1
            if retry_count == max_retries:
                results.append({"error": "Failed to analyze image after multiple retries"})
                break

    return results[0] if results else {"error": "Failed to analyze image"}

def categorize_topics(topics):
    prompt = {
        "role": "You are an expert in categorization and data organization.",
        "task": "Categorize the provided list of items into a minimal number of meaningful categories.",
        "input": topics,
        "output_format": {
            "type": "JSON",
            "structure": {
                "categories": [
                    {
                        "name": "Category Name",
                        "items": ["Item 1", "Item 2", "..."]
                    }
                ],
                "category_count": "Total number of categories created"
            }
        },
        "instructions": [
            "Analyze the provided list of items",
            "Create categories that group similar or related items together",
            "Aim to minimize the number of categories while maintaining meaningful distinctions",
            "Ensure each item is placed in the most appropriate category",
            "Provide descriptive and concise names for each category",
            "Include a count of the total number of categories created",
            "Return ONLY the JSON output, no additional text"
        ],
        "guidelines": [
            "Prioritize clarity and usefulness in your categorization",
            "Consider multiple aspects of the items when determining categories (e.g., function, theme, context)",
            "Be consistent in your categorization approach",
            "If an item could fit multiple categories, choose the most relevant or create a more specific category if necessary"
        ]
    }

    try:
        response = anthropic.messages.create(
            model="claude-3-sonnet-20240229",
            max_tokens=4096,
            messages=[
                {
                    "role": "user",
                    "content": json.dumps(prompt)
                }
            ]
        )

        response_content = response.content[0].text
        print(f"Raw API response: {response_content}")

        try:
            categorized_topics = json.loads(response_content)
            print(f"Parsed categorized topics: {categorized_topics}")
            return categorized_topics
        except json.JSONDecodeError as json_error:
            print(f"JSON parsing error: {str(json_error)}")
            print(f"Failed to parse JSON: {response_content}")
            return None

    except Exception as e:
        print(f"Error categorizing topics: {str(e)}")
        return None

def process_json_results(json_path):
    try:
        with open(json_path, 'r') as json_file:
            all_pages_analysis = json.load(json_file)

        common_topics = defaultdict(list)
        for page, analysis in all_pages_analysis.items():
            for topic in analysis.get('topics', []):
                common_topics[topic].append(page)

        # Categorize the common topics
        categorized_topics = categorize_topics(list(common_topics.keys()))

        result = {
            "common_topics": dict(common_topics),
            "categorized_topics": categorized_topics,
            "pages": all_pages_analysis
        }

        document_name = os.path.splitext(os.path.basename(json_path))[0]
        processed_json_path = f'/tmp/{document_name}_processed.json'
        with open(processed_json_path, 'w') as json_file:
            json.dump(result, json_file, indent=2)

        # Upload the processed JSON to S3
        s3_object_name = f'json_results/{document_name}_processed.json'
        upload_file_to_s3(processed_json_path, S3_BUCKET, s3_object_name)

        return s3_object_name

    except Exception as e:
        print(f"Error processing JSON results: {str(e)}")
        return None

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
                s3_client.head_object(Bucket=S3_BUCKET, Key=s3_processed_json_object)
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
                s3_client.head_object(Bucket=S3_BUCKET, Key=s3_json_object)
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
        s3_object = f'json_results/{filename}'
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
        s3_pdf_object = f'uploads/{filename}'
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