from flask import Flask, request, jsonify, render_template, session, redirect, url_for
import boto3
from botocore.exceptions import ClientError
import json
import logging
import atexit
import signal
import sys
import os
from dotenv import load_dotenv
from flask_babel import Babel, _
from datetime import datetime
import time
from flask_cors import CORS
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests

# Load environment variables from .env file
env_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(env_path)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Log environment variables at startup
logger.info("=== Application Startup ===")
logger.info(f"Environment file path: {env_path}")
logger.info(f"Environment file exists: {os.path.exists(env_path)}")
logger.info("=== Environment Variables ===")
logger.info(f"MAILTRAP_HOST: {os.getenv('MAILTRAP_HOST')}")
logger.info(f"MAILTRAP_PORT: {os.getenv('MAILTRAP_PORT')}")
logger.info(f"MAILTRAP_USERNAME: {os.getenv('MAILTRAP_USERNAME')}")
logger.info(f"MAILTRAP_PASSWORD exists: {'Yes' if os.getenv('MAILTRAP_PASSWORD') else 'No'}")
logger.info(f"SENDER_EMAIL: {os.getenv('SENDER_EMAIL')}")
logger.info(f"RECIPIENT_EMAIL: {os.getenv('RECIPIENT_EMAIL')}")
logger.info("===========================")

# Disable botocore debug logging
logging.getLogger('botocore').setLevel(logging.WARNING)
logging.getLogger('boto3').setLevel(logging.WARNING)

app = Flask(__name__)
CORS(app, supports_credentials=True)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'your-secret-key-here')
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# Internationalization config
app.config['BABEL_DEFAULT_LOCALE'] = 'en'
babel = Babel(app)

# Simple in-memory cache
cache = {}
CACHE_TTL = 60  # Cache time-to-live in seconds

# Email configuration
MAILTRAP_API_TOKEN = os.getenv('MAILTRAP_API_TOKEN')
MAILTRAP_INBOX_ID = os.getenv('MAILTRAP_INBOX_ID') # Your Mailtrap inbox ID
SENDER_EMAIL = "hello@demomailtrap.co"
RECIPIENT_EMAIL = "ylin090164@gmail.com"

# AWS SES Configuration
AWS_REGION = os.getenv('AWS_REGION', 'us-east-2')
ses_client = boto3.client('ses', region_name=AWS_REGION)


def get_locale():
    logger.info(f"Current session: {session}")
    # First check if language is set in session
    if 'language' in session:
        logger.info(f"Language from session: {session['language']}")
        return session['language']
    # Then check URL parameter
    lang = request.args.get('lang')
    if lang:
        logger.info(f"Language from URL parameter: {lang}")
        session['language'] = lang
        return lang
    # Finally, default to English
    logger.info("Using default language: en")
    return 'en'

babel.init_app(app, locale_selector=get_locale)

@app.route('/set-language', methods=['POST'])
def set_language():
    try:
        logger.info(f"Received language change request. Form data: {request.form}")
        language = request.form.get('language', 'en')
        logger.info(f"Selected language: {language}")
        
        if language in ['en', 'zh']:  # Only allow English and Chinese
            session['language'] = language
            logger.info(f"Language set in session: {session['language']}")
            return jsonify({'success': True, 'language': language})
        else:
            logger.warning(f"Invalid language attempted: {language}")
            return jsonify({'success': False, 'error': 'Invalid language'}), 400
            
    except Exception as e:
        logger.error(f"Error setting language: {str(e)}")
        logger.error(f"Error type: {type(e)}")
        logger.error(f"Error details: {e.__dict__ if hasattr(e, '__dict__') else 'No details available'}")
        return jsonify({'success': False, 'error': str(e)}), 500

# Create DynamoDB resource using IAM role
region = os.getenv('AWS_REGION', 'us-east-2')
dynamodb = boto3.resource('dynamodb', region_name=region)
table = dynamodb.Table('MyTable')

def cleanup():
    logger.info("Cleaning up resources...")

def signal_handler(sig, frame):
    logger.info("Received shutdown signal")
    cleanup()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)
atexit.register(cleanup)

@app.route('/')
def home():
    try:
        return render_template('dynamodb.html')
    except Exception as e:
        logger.error(f"Error rendering template: {str(e)}")
        return str(e), 500

@app.route('/works')
def works():
    try:
        return render_template('works.html')
    except Exception as e:
        logger.error(f"Error rendering works template: {str(e)}")
        return str(e), 500

@app.route('/contact')
def contact():
    try:
        return render_template('contact.html')
    except Exception as e:
        logger.error(f"Error rendering contact template: {str(e)}")
        return str(e), 500

@app.route('/item', methods=['POST'])
def create_item():
    try:
        data = request.get_json()
        if not data or 'comment' not in data:
            return jsonify({'error': 'No comment provided'}), 400
        
        # Generate a timestamp-based ID
        timestamp = int(time.time() * 1000)
        data['id'] = str(timestamp)
        data['timestamp'] = datetime.utcnow().isoformat()
        
        # Store the item
        table.put_item(Item=data)
        
        # Invalidate cache
        cache.clear()
        
        return jsonify({'message': 'Comment added', 'item': data}), 201
    except Exception as e:
        logger.error(f"Error creating item: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/item/<string:item_id>', methods=['GET'])
def read_item(item_id):
    try:
        response = table.get_item(Key={'id': item_id})
        item = response.get('Item')
        if item:
            return jsonify(item)
        else:
            return jsonify({'message': f"{_('read').capitalize()} not found"}), 404
    except Exception as e:
        logger.error(f"Error reading item: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/item/<string:item_id>', methods=['PUT'])
def update_item(item_id):
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400

        # Build update expression for all fields
        update_expression = "SET " + ", ".join(f"#{k}= :{k}" for k in data.keys())
        expression_values = {f":{k}": v for k, v in data.items()}
        expression_names = {f"#{k}": k for k in data.keys()}
        
        table.update_item(
            Key={'id': item_id},
            UpdateExpression=update_expression,
            ExpressionAttributeValues=expression_values,
            ExpressionAttributeNames=expression_names
        )
        return jsonify({'message': f"{_('update').capitalize()}d", 'item': {**data, 'id': item_id}})
    except ClientError as e:
        logger.error(f"Client error updating item: {str(e)}")
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Error updating item: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/item/<string:item_id>', methods=['DELETE'])
def delete_item(item_id):
    try:
        table.delete_item(Key={'id': item_id})
        return jsonify({'message': f"{_('delete').capitalize()}d"})
    except ClientError as e:
        logger.error(f"Client error deleting item: {str(e)}")
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Error deleting item: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/items')
def get_all_items():
    try:
        # Check cache first
        cache_key = 'all_items'
        if cache_key in cache and time.time() - cache[cache_key]['timestamp'] < CACHE_TTL:
            return jsonify(cache[cache_key]['data'])

        logger.info("Attempting to scan DynamoDB table: MyTable")
        # Query items with pagination
        response = table.scan(
            Limit=50  # Limit to 50 items per request
        )
        logger.info(f"DynamoDB scan response: {response}")
        
        items = response.get('Items', [])
        logger.info(f"Retrieved {len(items)} items from DynamoDB")
        
        # Sort items by timestamp in Python
        items.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
        
        # Cache the results
        cache[cache_key] = {
            'data': {'items': items},
            'timestamp': time.time()
        }
        
        return jsonify({'items': items})
    except Exception as e:
        logger.error(f"Error getting items: {str(e)}")
        logger.error(f"Error type: {type(e)}")
        logger.error(f"Error details: {e.__dict__ if hasattr(e, '__dict__') else 'No details available'}")
        return jsonify({'error': str(e)}), 500

@app.route('/delete-all', methods=['POST'])
def delete_all_items():
    try:
        # Get all items
        response = table.scan()
        items = response.get('Items', [])
        
        # Delete each item
        for item in items:
            table.delete_item(Key={'id': item['id']})
        
        # Clear cache
        cache.clear()
        
        return jsonify({'message': f'Deleted {len(items)} items'})
    except Exception as e:
        logger.error(f"Error deleting items: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/send-email', methods=['POST'])
def send_email():
    try:
        logger.info("=== Starting Email Send Process ===")
        data = request.get_json()
        logger.info(f"Received data: {data}")
        
        name = data.get('name')
        email = data.get('email')
        message = data.get('message')
        
        if not name or not email or not message:
            logger.error("Missing required fields")
            return jsonify({'error': 'Name, email, and message are required'}), 400

        # Create the email content
        email_content = f"""
        New message from Personal Website:
        
        Name: {name}
        Email: {email}
        Message: {message}
        
        Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
        """

        try:
            # Send email using Mailtrap API
            api_url = 'https://send.api.mailtrap.io/api/send'
            headers = {
                'Authorization': f'Bearer {MAILTRAP_API_TOKEN}',
                'Content-Type': 'application/json'
            }
            
            payload = {
                'to': [{'email': RECIPIENT_EMAIL}],
                'from': {'email': SENDER_EMAIL, 'name': 'Personal Website'},
                'subject': 'New Message from Personal Website',
                'text': email_content,
                'html': f'<p>{email_content.replace(chr(10), "<br>")}</p>',
                'category': 'Contact Form'
            }
            
            logger.info("Sending request to Mailtrap API...")
            logger.info(f"API URL: {api_url}")
            logger.info(f"Headers: {headers}")
            logger.info(f"Payload: {payload}")
            
            response = requests.post(api_url, json=payload, headers=headers)
            
            logger.info(f"API Response Status: {response.status_code}")
            logger.info(f"API Response: {response.text}")
            
            if response.status_code in [200, 201]:
                logger.info("Email sent successfully via Mailtrap API")
                return jsonify({'success': True, 'message': 'Email sent successfully'})
            else:
                logger.error(f"Mailtrap API Error: {response.status_code} - {response.text}")
                return jsonify({'error': f'Mailtrap API Error: {response.text}'}), 500
                
        except requests.exceptions.RequestException as e:
            logger.error(f"API Request Error:")
            logger.error(f"Error type: {type(e)}")
            logger.error(f"Error message: {str(e)}")
            if hasattr(e, 'response'):
                logger.error(f"Response status: {e.response.status_code}")
                logger.error(f"Response text: {e.response.text}")
            return jsonify({'error': f'API Request Error: {str(e)}'}), 500
        except Exception as e:
            logger.error(f"Unexpected Error in API process:")
            logger.error(f"Error type: {type(e)}")
            logger.error(f"Error message: {str(e)}")
            return jsonify({'error': f'Unexpected Error: {str(e)}'}), 500

    except Exception as e:
        logger.error(f"General Error in send_email:")
        logger.error(f"Error type: {type(e)}")
        logger.error(f"Error message: {str(e)}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    try:
        app.run(port=5003, debug=True)
    except Exception as e:
        logger.error(f"Error starting Flask app: {str(e)}")
        cleanup()
        sys.exit(1)
