from flask import request, jsonify, make_response, send_file
import os

# Default sprites list (replace "spriteList..." with actual values)
default_sprites = [
    "spriteList..."
]

def assets_route():
    # Set CORS headers
    response = make_response()
    response.headers["Access-Control-Allow-Origin"] = os.getenv('HOSTED_ON')
    response.headers["Access-Control-Allow-Credentials"] = "true"

    if request.method == 'POST':  # Update Asset
        # Extract the payload path from the request URI
        payload = request.url[request.url.find('?/') + 2:]
        payload_path = f'ProjectData/ProjectAssets/{payload}'
        
        # Read the raw request data
        data = request.get_data()
        if not data:
            return jsonify({"status": "error", "error": "No payload given"}), 500

        # Write data to the specified file
        try:
            with open(payload_path, 'wb') as file:
                file.write(data)
            
            if os.path.exists(payload_path):
                return jsonify({"status": "ok", "content-name": payload_path}), 200
            else:
                return jsonify({"status": "error", "error": "Could not save file"}), 500
        except Exception as e:
            return jsonify({"status": "error", "error": str(e)}), 500

    elif request.method == 'GET':  # Returns requested assets (costume, audio, etc.)
        # Extract the payload path from the request URI
        payload = request.url[request.url.find('?/') + 2:]
        if not payload:
            return jsonify({"status": "error", "error": "No file requested"}), 500

        # Remove `internalapi/asset/` from payload
        payload = payload.replace('internalapi/asset/', '')

        # Check if payload structure is valid
        if '/get/' not in payload:
            return jsonify({"status": "error", "error": "unknown request structure"}), 500

        # Clean up the payload
        payload = payload.replace('/get/', '').replace('=', '')

        # Check if payload is in default sprites
        if payload in default_sprites:
            sprite_path = f'defaultSprites/{payload}'
            try:
                return send_file(sprite_path)
            except FileNotFoundError:
                return jsonify({"status": "error", "error": "file not found"}), 404

        # Check for the requested file in ProjectAssets
        payload_path = f'ProjectData/ProjectAssets/{payload}'
        if os.path.exists(payload_path):
            try:
                return send_file(payload_path)
            except FileNotFoundError:
                return jsonify({"status": "error", "error": "file not found"}), 404
        else:
            return jsonify({"status": "error", "error": "file not found"}), 404

    else:
        # Method not allowed
        return jsonify({"status": "error", "error": "method not allowed"}), 405
