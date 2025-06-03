from flask import request, jsonify
import os
import json

from API.services.helpers import get_db_connection

def update_collaborators_route(): 
    if request.method == 'OPTIONS':
        return '', 204

    if not request.is_json:
        return jsonify({"error": "Request must be JSON"}), 415 # Unsupported Media Type

    token = request.json.get('token')
    if token != os.getenv("InternalAPIKey"):
        return jsonify({"error": "Invalid token"}), 401

    projectID = request.json.get('projectID')
    username = request.json.get('username') # The collaborator's username
    action = request.json.get('action')     # "add" or "remove"

    if not all([projectID, username, action]):
        return jsonify({"error": "Missing projectID, username, or action"}), 400

    if action.lower() not in ["add", "remove"]:
        return jsonify({"error": "Invalid action. Must be 'add' or 'remove'"}), 400

    action = action.lower() # Normalize action to lowercase

    # Step 1: Establish DB connection
    try:
        db_connection = get_db_connection("projects")
        cursor = db_connection.cursor(dictionary=True)

        # Step 2: Fetch current collaborators for the project
        cursor.execute("SELECT Collaborators FROM projects WHERE projectID = %s", (projectID,))
        project_data = cursor.fetchone()

        if not project_data:
            cursor.close()
            db_connection.close()
            return jsonify({"error": "Project not found"}), 404

        current_collaborators_json = project_data['Collaborators']
        
        # Initialize collaborators_list
        if current_collaborators_json is None:
            collaborators_list = []
        else:
            try:
                # Ensure it's a string before trying to load, MySQL/MariaDB JSON type might return it parsed
                if isinstance(current_collaborators_json, (list, dict)): # Already parsed by connector
                    collaborators_list = list(current_collaborators_json) if isinstance(current_collaborators_json, list) else []
                elif isinstance(current_collaborators_json, str):
                    collaborators_list = json.loads(current_collaborators_json)
                else: # Should not happen, but good to be safe
                    collaborators_list = []
                
                if not isinstance(collaborators_list, list):
                    # If DB contains non-array JSON (e.g. a string "username"), reset to empty list
                    # Or handle as an error, depending on desired robustness.
                    # For now, we'll assume it should be a list or can be reset.
                    collaborators_list = []
            except json.JSONDecodeError:
                # If JSON is corrupted, start fresh or return an error
                collaborators_list = [] 

        # Step 3: Perform action (add or remove)
        original_collaborators_count = len(collaborators_list)
        action_performed_message = ""

        if action == "add":
            if username not in collaborators_list:
                collaborators_list.append(username)
                action_performed_message = f"Collaborator '{username}' added to project {projectID}."
            else:
                action_performed_message = f"Collaborator '{username}' already in project {projectID}."
        
        elif action == "remove":
            if username in collaborators_list:
                collaborators_list.remove(username)
                action_performed_message = f"Collaborator '{username}' removed from project {projectID}."
            else:
                action_performed_message = f"Collaborator '{username}' not found in project {projectID}."

        # Step 4: Update the database if changes were made
        # Only update if the list content actually changed
        if len(collaborators_list) != original_collaborators_count or \
           (action == "add" and username in collaborators_list and len(collaborators_list) == original_collaborators_count + 1) or \
           (action == "remove" and username not in collaborators_list and len(collaborators_list) == original_collaborators_count -1 ):

            # If the list is empty, store NULL, otherwise store the JSON string
            new_collaborators_db_val = json.dumps(collaborators_list) if collaborators_list else None
            
            query = """
            UPDATE projects
            SET Collaborators = %s,
                EditTS = CURRENT_TIMESTAMP
            WHERE projectID = %s
            """
            cursor.execute(query, (new_collaborators_db_val, projectID))
            db_connection.commit()
            status_message = "success"
        else:
            # No actual change to the list (e.g., tried to add existing, or remove non-existing)
            status_message = "no_change"


        # Step 5: Return success response
        return jsonify({
            "status": status_message,
            "info": action_performed_message,
            "projectID": projectID,
            "collaborators": collaborators_list # Return the updated list
        }), 200

    except Exception as e:
        print(f"Error in update_collaborators_route: {e}")
        if 'db_connection' in locals() and db_connection.is_connected():
            db_connection.rollback()
        return jsonify({"error": "An internal server error occurred"}), 500
    finally:
        if 'cursor' in locals() and cursor:
            cursor.close()
        if 'db_connection' in locals() and db_connection.is_connected():
            db_connection.close()