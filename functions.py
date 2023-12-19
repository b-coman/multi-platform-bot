import json
import os

def create_assistant(client):
  assistant_file_path = 'assistant.json'

  if os.path.exists(assistant_file_path):
    with open(assistant_file_path, 'r') as file:
      assistant_data = json.load(file)
      assistant_id = assistant_data['assistant_id']
      print("Loaded existing assistant ID.")
  else:
    file = client.files.create(file=open("Comarnic Property Guide-14.pdf", "rb"),
                               purpose='assistants')

    assistant = client.beta.assistants.create(instructions="""
         Your name is Bo, and you are the butler for Mointain Chalet, Bogdan's property located at Secăriei Street no 197, Comarnic. Your role is to provide guests with a warm and engaging experience, helping with all details they need during their stay. When offering information about the property, its amenities, and activities, you'll use varied terms for a welcoming feel and provide web links for additional details. Occasionally, use emojis in your responses where appropriate to enhance friendliness. You advise guests to contact Bogdan for specific requests or malfunctions, or when you can't find the information in your training data. While communicating in Romanian and English, you avoid personal topics, maintaining a professional yet friendly demeanor. Your responses are informative, reflecting the chalet's welcoming atmosphere. After responses, you either remain silent or subtly introduce a connected topic, maintaining a friendly but not servile tone.
          """,
                                              model="gpt-4-1106-preview",
                                              tools=[{
                                                  "type": "retrieval"
                                              }],
                                              file_ids=[file.id])

    with open(assistant_file_path, 'w') as file:
      json.dump({'assistant_id': assistant.id}, file)
      print("Created a new assistant and saved the ID.")

    assistant_id = assistant.id

  return assistant_id
