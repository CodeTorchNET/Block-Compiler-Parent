from flask import request, jsonify, make_response
import os
import json
import re

def projectsMETA_route():
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
        # Extract payload from URL
        uri = request.url
        payload_index = uri.find('?/')
        if payload_index != -1:
            payload = uri[payload_index + 2:]
        else:
            payload = ""

        # Validate payload contains only numbers
        if not payload.isdigit():
            return jsonify({"status": "error", "error": "Invalid requested file"}), 500

        # Read the input data
        data = request.data
        if not data:
            return jsonify({"status": "error", "error": "No payload given"}), 500

        # Save data to the specified JSON file
        filepath = f'ProjectData/ProjectData/{payload}.json'
        try:
            with open(filepath, 'wb') as file:
                file.write(data)

            if os.path.exists(filepath):
                return jsonify({"status": "ok", "autosave-interval": "120"}), 200
            else:
                return jsonify({"status": "error", "error": "Could not save file"}), 500
        except Exception as e:
            return jsonify({"status": "error", "error": f"Could not save file: {str(e)}"}), 500

    elif request.method == 'GET':
        # Respond with static metadata
        metadata = {
            "id": "32",
            "title": "Waterfall Plains v1.0",
            "description": "%3Cp%3EPart%20of%20%40Titanium01%20's%20Scratch%20Project%20Archive%3C%2Fp%3E",
            "visibility": "true",
            "public": "true",
            "comments_allowed": "true",
            "is_published": "true",
            "tags": "",
            "author": {
                "DisplayName": "Titanium",
                "username": "Titanium",
                "history": {"joined": "1900-01-01T00:00:00.000Z"},
                "profile": {
                    "id": None,
                    "images": {
                        "90x90": "/IMGStorage/Logos/Titanium.jpeg?width=90&height=90",
                        "60x60": "/IMGStorage/Logos/Titanium.jpeg?width=60&height=60",
                        "55x55": "/IMGStorage/Logos/Titanium.jpeg?width=55&height=55",
                        "50x50": "/IMGStorage/Logos/Titanium.jpeg?width=50&height=50",
                        "32x32": "/IMGStorage/Logos/Titanium.jpeg?width=32&height=32",
                    },
                },
            },
            "linked": 0,
            "history": {
                "created": 1719196563780,
                "modified": 1719264762146,
                "shared": 1719196614032,
            },
            "stats": {"views": 6, "loves": 1, "likes": 1, "remixes": 0},
            "remixed": {"remixed": "", "id": 0, "project": []},
            "project_token": "NotApplicable",
            "canEdit": "false",
            "personalData": {
                "isLoved": "false",
                "isLiked": "false",
                "isDisliked": "false",
                "YouFollow": "false",
                "yourPFP": "DPFP.png",
            },
        }
        return jsonify(metadata)

    else:
        # Return error for unsupported methods
        return jsonify({"status": "error", "error": "method not implemented"}), 405
