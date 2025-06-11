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

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)  # Changed from DEBUG to INFO
logger = logging.getLogger(__name__)

# Disable botocore debug logging
logging.getLogger('botocore').setLevel(logging.WARNING)
logging.getLogger('boto3').setLevel(logging.WARNING)

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'your-secret-key-here')  # Required for session

# Internationalization config
app.config['BABEL_DEFAULT_LOCALE'] = 'en'
babel = Babel(app)

def get_locale():
    # First check for lang parameter in URL
    lang = request.args.get('lang')
    if lang:
        session['language'] = lang
        return lang
    # Fall back to session value
    return session.get('language', 'en')

babel.init_app(app, locale_selector=get_locale)

@app.route('/set-language', methods=['POST'])
def set_language():
    language = request.form.get('language', 'en')
    session['language'] = language
    return redirect(url_for('home'))

# Configure AWS credentials from environment variables
aws_access_key = os.getenv('AWS_ACCESS_KEY_ID')
aws_secret_key = os.getenv('AWS_SECRET_ACCESS_KEY')
region = os.getenv('AWS_REGION', 'us-east-2')

if not aws_access_key or not aws_secret_key:
    raise ValueError("AWS credentials not found in environment variables")

# Create DynamoDB resource with credentials
dynamodb = boto3.resource(
    'dynamodb',
    region_name=region,
    aws_access_key_id=aws_access_key,
    aws_secret_access_key=aws_secret_key
)
table = dynamodb.Table('MyTable')

def cleanup():
    logger.info("Cleaning up resources...")
    # Add any cleanup code here if needed

def signal_handler(sig, frame):
    logger.info("Received shutdown signal")
    cleanup()
    sys.exit(0)

# Register the signal handler
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# Register cleanup function
atexit.register(cleanup)

@app.route('/')
def home():
    try:
        return render_template('dynamodb.html',
                               create_word=_('create'),
                               read_word=_('read'),
                               update_word=_('update'),
                               delete_word=_('delete'))
    except Exception as e:
        logger.error(f"Error rendering template: {str(e)}")
        return str(e), 500

@app.route('/item', methods=['POST'])
def create_item():
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        if 'id' not in data:
            return jsonify({'error': 'ID is required'}), 400

        # Store the entire data object as is
        table.put_item(Item=data)
        return jsonify({'message': f"{_('create').capitalize()}d", 'item': data}), 201
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
        response = table.scan()
        items = response.get('Items', [])
        return jsonify({'items': items})
    except Exception as e:
        logger.error(f"Error getting all items: {str(e)}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    try:
        app.run(port=5001, debug=True)
    except Exception as e:
        logger.error(f"Error starting Flask app: {str(e)}")
        cleanup()
        sys.exit(1)
