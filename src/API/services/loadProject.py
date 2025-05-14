from flask import jsonify
import json

from API.services.helpers import get_db_connection, verifyToken, limiter

@limiter.limit("5 per minute")
def loadProject(project_id, request_obj):
    """
    Loads a project from a JSON file based on the project ID.
    Checks if the project is public, or if the user is the owner,
    or if the user is a collaborator.
    Returns the project data if found and accessible, otherwise returns an error.
    """
    db_connection = None
    cursor = None

    try:
        # Step 1: Establish DB connection
        db_connection = get_db_connection()
        cursor = db_connection.cursor(dictionary=True)

        # Step 2: Query the database for the project
        query = "SELECT Owner, isShared, Collaborators FROM projects WHERE projectID = %s"
        cursor.execute(query, (project_id,))
        project = cursor.fetchone()

        if not project:
            return jsonify({
                "status": "error",
                "message": "Invalid project ID"
            }), 404

        # Step 3: Check access permissions
        can_access = False
        
        if project['isShared'] == 1: # Project is public
            can_access = True
        else: # Project is private, check token against owner and collaborators
            user_token = request_obj.args.get('token')
            if not user_token:
                return jsonify({
                    "status": "error",
                    "message": "Project is private and requires a token for access."
                }), 401

            # Check if token belongs to the owner
            if verifyToken(user_token, project['Owner']):
                can_access = True
            else:
                # If not owner, check if token belongs to any collaborator
                collaborators_json = project['Collaborators']
                if collaborators_json:
                    collaborators_list = []
                    # Ensure collaborators_json is handled correctly (string or already parsed list)
                    if isinstance(collaborators_json, str):
                        try:
                            collaborators_list = json.loads(collaborators_json)
                        except json.JSONDecodeError:
                            print(f"Warning: Malformed JSON in Collaborators for projectID {project_id}")
                            # collaborators_list remains empty if JSON is bad
                    elif isinstance(collaborators_json, list): # If DB driver already parsed it
                        collaborators_list = collaborators_json
                    
                    # Ensure collaborators_list is indeed a list before iterating
                    if isinstance(collaborators_list, list):
                        for collaborator_username in collaborators_list:
                            if verifyToken(user_token, collaborator_username):
                                can_access = True
                                break # Found a valid collaborator token

        if not can_access:
            return jsonify({
                "status": "error",
                "message": "Access denied. Invalid token or insufficient permissions."
            }), 403

        # Step 4: Load the project data from the file if access is granted
        file_path = f'storage/projectData/projectData/{project_id}.json'
        try:
            with open(file_path, 'r') as file:
                data = json.load(file)
            return jsonify(data), 200 # Added 200 OK status
        
        except FileNotFoundError:
            return jsonify({
                "status": "error",
                "message": "Project data file does not exist"
            }), 404
        except json.JSONDecodeError:
            return jsonify({
                "status": "error",
                "message": "Failed to parse project data"
            }), 500

    except Exception as err:
        # Log the error for debugging. In a production app, use a proper logger.
        print(f"An unexpected error occurred in loadProject (project_id: {project_id}): {err}")
        # Avoid sending raw str(err) to the client in production if it might leak sensitive info.
        return jsonify({
            "status": "error",
            "message": "An internal server error occurred." # Generic message
            # "details": str(err) # Optionally, for debugging environments
        }), 500
    finally:
        if cursor:
            cursor.close()
        if db_connection:
            db_connection.close()