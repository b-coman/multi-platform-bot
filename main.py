import os
import json
from time import sleep
from packaging import version
from flask import Flask, request, jsonify
import requests
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
import openai
from openai import OpenAI
import functions
import re
import logging
from logging.handlers import RotatingFileHandler
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from collections import deque

# Initialize Flask app
app = Flask(__name__)

# A dictionary to hold queues for each Slack channel
message_queues = {}

# Global dictionary to manage Slack threads
slack_threads = {}

# Global dictionary to track active threads
active_threads = {}

# Initialize Slack client
slack_bot_token = os.environ.get('SLACK_BOT_TOKEN')
if not slack_bot_token:
    raise ValueError("Error: Slack bot token not found in environment variables.")
slack_client = WebClient(token=slack_bot_token)

# Initialize Twilio client
twilio_client = Client(os.environ['TWILIO_ACCOUNT_SID'], os.environ['TWILIO_AUTH_TOKEN'])

# Set up logging
handler = RotatingFileHandler('app.log', maxBytes=10000, backupCount=1)
handler.setLevel(logging.INFO)
formatter = logging.Formatter('[%(asctime)s] %(levelname)s in %(module)s: %(message)s')
handler.setFormatter(formatter)
app.logger.addHandler(handler)
app.logger.setLevel(logging.INFO)

# Function to send a GET request to retrieve data
def get_data_from_google_sheets(client_name):
    url = "https://script.google.com/macros/s/AKfycbzxFhwF8MAkuFKm_JW0XbDTCB1dgJpYx9D-_q5NnBNL5B2rY9Z0RVzgp-xtZganXT1v/exec"
    params = {"clientName": client_name}
    response = requests.get(url, params=params)
    return response.json()

# Function to send a POST request to update data
def update_data_to_google_sheets(client_name, data_to_update):
    url = "https://script.google.com/macros/s/AKfycbzxFhwF8MAkuFKm_JW0XbDTCB1dgJpYx9D-_q5NnBNL5B2rY9Z0RVzgp-xtZganXT1v/exec"
    data = {
        "clientName": client_name,
        "dataToUpdate": data_to_update
    }
    response = requests.post(url, json=data)
    return response.status_code

# Function to remove citations
def remove_citations(response):
    start_pattern = "【"
    end_pattern = "】"
    while start_pattern in response and end_pattern in response:
        start_index = response.find(start_pattern)
        end_index = response.find(end_pattern, start_index)
        if end_index != -1:
            response = response[:start_index] + response[end_index + len(end_pattern):]
        else:
            break
    return response

# Validate OpenAI API key
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
if not OPENAI_API_KEY:
    raise ValueError("Error: OPENAI_API_KEY environment variable is missing.")

# Check OpenAI version
required_version = version.parse("1.1.1")
current_version = version.parse(openai.__version__)
if current_version < required_version:
    raise ValueError(f"Error: OpenAI version {openai.__version__} is less than the required version 1.1.1")

# Initialize OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY)

# Load assistants
assistant_id = "asst_YYEm9gb9iOWwYFzolmBpLero"
classifier_assistant_id = "asst_0UXrgJhmfKv7Fq9W6l6Cq6bo"  


# Global dictionary for WhatsApp thread IDs
whatsapp_threads = {}

# Dictionary to store user-specific threads
user_threads = {}

# Route for starting a conversation
@app.route('/start', methods=['GET'])
def start_conversation():
    try:
        thread = client.beta.threads.create()
        return jsonify({"thread_id": thread.id})
    except Exception as e:
        app.logger.error(f"Error creating conversation thread: {e}")
        return jsonify({"error": "Failed to start conversation"}), 500

# Route for handling chat messages
@app.route('/chat', methods=['POST'])
def chat():
    data = request.json
    thread_id = data.get('thread_id')
    user_input = data.get('message', '')

  # classifier starts here
  # Define the instructions for the classifier
    instructions = (
        "You are a classifier assistant used to analyse a request. "
        "You analyse the request and decide if it relates to a general inquiry, "
        "it refers to a client related topic or is a technical issue. "
        "The values are: general, client, technical. "
        "Please estimate the confidence score on a scale from 0 to 1."
        "Return the classification in JSON format with 'classification' and 'confidence' fields."
    )
  
    # Making the API call for classification
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",  # Specify the model you're using
            messages=[
                {
                    "role": "user",
                    "content": f"{instructions}\n\nRequest: {user_input}"
                }
            ]
        )
  
        # Extracting the text response for classification
        classification_result = response.choices[0].message.content

        # Parse the JSON response
        try:
            classification_data = json.loads(classification_result)
        except json.JSONDecodeError:
            app.logger.error("Error decoding JSON from the classification result")
            return jsonify({"error": "Failed to parse classification result"}), 500

        classification = classification_data.get("classification", "")
        confidence = classification_data.get("confidence", 0)
        app.logger.info(classification)
        app.logger.info(confidence)
  
    except Exception as e:
        app.logger.error(f"Error during classification: {e}")
        return jsonify({"error": "Failed to classify message"}), 500
  
    # /classification ends

    # Add any additional instructions or context to the classified input
    # Logic to set classification_context
    classification_context = ""
    if classification == "general" or confidence < 0.6:
         classification_context = "this is a general inquiry; make your best guess using your general skills and knowledge. If you are unsure, ask for clarifications."
    if classification == "technical":
         classification_context = "this is a technical an inquiry about Aha, and maybe other aspects of digital transformation initiatives; reserach on the internet, and in your general knowledge about the way Aha works."
    if classification == "client":
         classification_context = "this is an inquiry about a client related issue; be as much as possible focused on your context you have, the knowledge base provided to you: MC-Fantastic's best practices, past project debriefs, engagement definitions; when you create the response extract contextual information from there, and provide those back in your response, including specific refrences and client names from the files provided to you; if the situation is unclear, provide the user questions that can be asked."

    user_input = "This is the user's ask:'" + user_input + "' \n This is context for Aha Sales Genie assistant: " + classification_context
    app.logger.info(user_input)


    # Check if the user has an associated thread
    user_id = data.get('user_id')  # You may need to define how to get the user's ID from the request
    if user_id not in user_threads:
        # Create a new thread for the user
        try:
            thread = client.beta.threads.create()
            user_threads[user_id] = thread.id
        except Exception as e:
            app.logger.error(f"Error creating conversation thread for user {user_id}: {e}")
            return jsonify({"error": "Failed to start conversation"}), 500

    # Use the user-specific thread ID
    user_thread_id = user_threads[user_id]
    #app.logger.info(user_input)

    if not user_thread_id:
        return jsonify({"error": "Missing thread_id"}), 400

    try:
        client.beta.threads.messages.create(thread_id=user_thread_id, role="user", content=user_input)
        run = client.beta.threads.runs.create(thread_id=user_thread_id, assistant_id=assistant_id)

        for _ in range(60):  # 60 seconds timeout
            run_status = client.beta.threads.runs.retrieve(thread_id=user_thread_id, run_id=run.id)
            if run_status.status == 'completed':
                messages = client.beta.threads.messages.list(thread_id=user_thread_id)
                response = messages.data[0].content[0].text.value
                cleaned_response = remove_citations(response)

                # Check if the user input contains a request to fetch data from Google Sheets
                if "retrieve data for client" in user_input:
                    # Extract the client name from the user input
                    client_name = re.search(r'retrieve data for client (.+)', user_input).group(1)

                    # Get data from Google Sheets
                    google_sheets_data = get_data_from_google_sheets(client_name)

                    # Add Google Sheets data to the response
                    cleaned_response += f"\n\nData from Google Sheets:\n{google_sheets_data}"

                return jsonify({"response": cleaned_response})
            sleep(1)
        app.logger.warning("Response timeout reached")
        return jsonify({"error": "Response timeout"}), 504
    except Exception as e:
        app.logger.error(f"Error during conversation: {e}")
        return jsonify({"error": "Failed to process message"}), 500

# Route for receiving Slack messages
@app.route('/slack/events', methods=['POST'])
def slack_events():
    data = request.json

    # Slack URL Verification Challenge
    if data.get('type') == 'url_verification':
        app.logger.info('Received Slack URL verification challenge.')
        return jsonify({'challenge': data['challenge']})

    # Handle Slack Events
    if data.get('type') == 'event_callback':
        event = data.get('event', {})
        channel_id = event.get('channel')

        # Initialize the queue for the channel if it doesn't exist
        if channel_id not in message_queues:
            message_queues[channel_id] = deque()

        # Ignore bot messages and echoes to avoid loops
        if event.get('subtype') == 'bot_message' or event.get('bot_id'):
           # app.logger.info(f"Ignoring bot message or echo: {event}")
            return jsonify({'status': 'ignored'})

        if event.get('type') == 'message':
            text = event.get('text')
            app.logger.info(f"Received message from Slack: {text}")

            # Add the message to the channel's queue
            message_queues[channel_id].append(text)

            # Generate thread ID and check if it's active
            thread_id = 'slack_' + channel_id
            if thread_id not in slack_threads:
                try:
                    thread = client.beta.threads.create()
                    slack_threads[thread_id] = thread.id
                except Exception as e:
                    app.logger.error(f"Error creating conversation thread: {e}")
                    return jsonify({"error": "Failed to create conversation thread"}), 500

            if active_threads.get(thread_id):
                app.logger.info(f"Thread {thread_id} is active. Message queued.")
                return jsonify({'status': 'queued'}), 200

            # Mark the thread as active
            active_threads[thread_id] = True

            user_id = event.get('bot_id')
            app.logger.info(f"userID is {thread_id}.")

            # Send the typing indicator
            try:
              slack_client.chat_postMessage(
                  channel=channel_id,
                  text="... I'm thinking - wip project, it takes time :)",
                  as_user=True
              )
            except SlackApiError as e:
              app.logger.error(f"Error sending typing indicator to Slack: {e.response['error']}")


            # Prepare the message payload for the /chat route
            chat_payload = {
                'thread_id': slack_threads[thread_id],  # Use the thread ID from the Slack threads dictionary
                'message': text,
                'source': 'slack'
            }

            # Forward the message to the /chat endpoint
            response = requests.post('http://localhost:8080/chat', json=chat_payload)

            # Handle response and send back to Slack
            if response.status_code == 200:
                chat_response = response.json().get('response', 'No response')
                # Check if the OpenAI response contains '**' and format it as bold in Slack
                if '**' in chat_response:
                  formatted_response = chat_response.replace('**', '*')  # Replace '**' with '*' for bold formatting
                else:
                  formatted_response = chat_response

                try:
                    slack_client.chat_postMessage(channel=channel_id, text=formatted_response)
                except SlackApiError as e:
                    app.logger.error(f"Error sending message to Slack: {e.response['error']}")
            else:
                app.logger.error("Failed to process message through /chat route")

            # After processing, mark the thread as not active
            active_threads[thread_id] = False

    return jsonify({'status': 'success'})



if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
