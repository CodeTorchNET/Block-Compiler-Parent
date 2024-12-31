from flask import request, jsonify, make_response
import os
import json

def projects_route(subpath=""):
    if subpath == '':
        return jsonify({"status": "error", "error": "project path not provided"}), 400
    # Set CORS headers
    response = make_response()
    response.headers["Access-Control-Allow-Origin"] = os.getenv('HOSTED_ON')
    response.headers["Access-Control-Allow-Credentials"] = "true"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS, PUT"
    response.headers["Access-Control-Allow-Headers"] = "x-requested-with,x-token,accept-language,accept,accept-version,content-type,request-id,origin,x-api-version,x-request-id"

    # Handle OPTIONS requests
    if request.method == 'OPTIONS':
        return response

    elif request.method == 'PUT':
        # Save to server logic (authentication and saving JSON file)
        # Change project edit timestamp (logic to be implemented)
        return jsonify({"status": "ok", "autosave-interval": "120"})

    elif request.method == 'GET':
        # Logic to check if project is public or accessible (to be implemented)
        # Read JSON file from the server

        try:
            with open(f'app/internalAPI/projectData/projectData/1.json', 'r') as file:
                data = json.load(file)
        except FileNotFoundError:
            return jsonify({"status": "error", "error": "torch does not exist"}), 404

        return jsonify(data)

    elif request.method == 'POST':
        # Logic to ensure the user is authorized to create a new project (to be implemented)
        # Logic to update DB & files for project creation (to be implemented)
        return jsonify({"status": "ok", "content-name": "1", "content-title": "untitled", "autosave-interval": "120"})

    else:
        return jsonify({"status": "error", "error": "method not implemented"}), 405
