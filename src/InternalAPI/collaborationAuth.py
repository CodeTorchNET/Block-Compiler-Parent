from flask import request, jsonify
import os
import json

from API.services.helpers import get_db_connection, verifyToken

def collaborationAuth_routes():
    # Handle preflight request for CORS
    if request.method == 'OPTIONS':
        return '', 204
    
    # Validate the internal API token from the Yjs server
    internal_token = request.json.get('token') 
    if internal_token != os.getenv("InternalAPIKey"):
        return jsonify({"status": "error", "message": "Invalid internal API token"}), 401

    # Extract user and room information from the request
    user_id = request.json.get('user_id')
    user_token = request.json.get('user_token')
    room_id = request.json.get('roomID')

    # Basic input validation
    if not user_id or not user_token or not room_id:
        return jsonify({"status": "error", "message": "Missing user_id, user_token, or roomID"}), 400
    

    db_connection = None
    cursor = None

    try:
        # Step 1: Verify the client's user token
        is_user_token_valid = verifyToken(user_token, user_id)

        if not is_user_token_valid:
            print(f"[AUTH] User token invalid or expired for user '{user_id}'.")
            return jsonify({"status": "error", "message": "Invalid or expired user token"}), 403
        
        # Step 2: Retrieve project metadata for authorization
        db_connection = get_db_connection("projects")
        cursor = db_connection.cursor(dictionary=True)

        query = "SELECT Owner, isShared, Collaborators FROM projects WHERE CRoomID = %s"
        cursor.execute(query, (room_id,))
        project = cursor.fetchone()

        if not project:
            print(f"[AUTH] Collaboration room '{room_id}' not found.")
            return jsonify({"status": "error", "message": "Collaboration room not found"}), 404

        collaborators_list = []
        if project['Collaborators']:
            if isinstance(project['Collaborators'], str):
                try:
                    collaborators_list = json.loads(project['Collaborators'])
                except json.JSONDecodeError:
                    print(f"Warning: Malformed JSON in Collaborators for projectID {room_id}")
                    pass 
            elif isinstance(project['Collaborators'], list):
                collaborators_list = project['Collaborators']
        
        if not isinstance(collaborators_list, list):
            collaborators_list = []

        is_owner = user_id == project['Owner']
        is_collaborator = user_id in collaborators_list

        if is_owner or is_collaborator:
            print(f"[AUTH] User '{user_id}' authorized as {'owner' if is_owner else 'collaborator'} for private project '{room_id}'.")
        else:
            print(f"[AUTH] User '{user_id}' not authorized for private project '{room_id}'.")
            return jsonify({
                "status": "error",
                "message": "Unauthorized access to private project."
            }), 403

        return jsonify({
            "status": "success",
            "message": "Authentication and authorization successful",
            "username": user_id,
        }), 200

    except Exception as err:
        print(f"[ERROR] Unexpected error in collaborationAuth_routes for projectID {room_id_str}: {err}")
        return jsonify({"status": "error", "message": "An unexpected server error occurred."}), 500
    finally:
        if cursor:
            cursor.close()
        if db_connection:
            db_connection.close()