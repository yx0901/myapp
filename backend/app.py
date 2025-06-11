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

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Disable botocore debug logging
logging.getLogger('botocore').setLevel(logging.WARNING)
logging.getLogger('boto3').setLevel(logging.WARNING)

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'your-secret-key-here')

# Internationalization config
app.config['BABEL_DEFAULT_LOCALE'] = 'en'
babel = Babel(app)

# Simple in-memory cache
cache = {}
CACHE_TTL = 60  # Cache time-to-live in seconds

def get_locale():
    lang = request.args.get('lang')
    if lang:
        session['language'] = lang
        return lang
    return session.get('language', 'en')

babel.init_app(app, locale_selector=get_locale)

@app.route('/set-language', methods=['POST'])
def set_language():
    language = request.form.get('language', 'en')
    session['language'] = language
    return redirect(url_for('home'))

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

if __name__ == '__main__':
    try:
        app.run(port=5003, debug=True)
    except Exception as e:
        logger.error(f"Error starting Flask app: {str(e)}")
        cleanup()
        sys.exit(1)
