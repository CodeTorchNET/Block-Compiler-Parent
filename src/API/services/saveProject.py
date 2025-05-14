import os
import json 
from flask import jsonify

from API.services.helpers import get_db_connection, verifyToken, limiter


def internalSaveProject(request, project_id):
    # check if project exists
    project_data = request.json
    if(project_data is None):
        return jsonify({"status": "error", "error": "no project data provided"}), 400
    project_file_path = f'storage/projectData/projectData/{project_id}.json'

    if not os.path.exists(project_file_path):
        return jsonify({"status": "error", "error": "project does not exist"}), 404
    try:
        # Save updated project data
        with open(project_file_path, 'w') as file:
            json.dump(project_data, file, indent=4)  # indent=4 for pretty printing
    except (IOError, json.JSONDecodeError) as e:
        return jsonify({"status": "error", "error": str(e)}), 500
    
    return jsonify({"status": "ok", "autosave-interval": "120"})


@limiter.limit("10 per minute")
def saveProject(request, project_id):
    # (FUTURE) prevent user from uploading random JSON (validate JSON format)

    # Step 1: Establish DB connection
    try:
        token = request.args.get('token')
        if not token:
            return jsonify({
                "status": "error",
                "message": "Missing token"
            }), 400
        db_connection = get_db_connection()
        cursor = db_connection.cursor(dictionary=True)

        # Step 2: Query the database for the project owner and collaborators
        query = "SELECT Owner, Collaborators FROM projects WHERE projectID = %s"
        cursor.execute(query, (project_id,))
        project_db_info = cursor.fetchone()

        if not project_db_info:
            return jsonify({
                "status": "error",
                "message": "Invalid project ID"
            }), 404
        
        # Step 3: Verify the token against owner or collaborators
        can_save = False
        # Check if token belongs to the owner
        if verifyToken(token, project_db_info['Owner']):
            can_save = True
        else:
            # If not owner, check if token belongs to any collaborator
            collaborators_json = project_db_info['Collaborators']
            if collaborators_json:
                collaborators_list = []
                if isinstance(collaborators_json, str):
                    try:
                        collaborators_list = json.loads(collaborators_json)
                    except json.JSONDecodeError:
                        print(f"Warning: Malformed JSON in Collaborators for projectID {project_id}")
                        pass # Treat as no collaborators if JSON is bad
                elif isinstance(collaborators_json, list): # If DB driver already parsed it
                    collaborators_list = collaborators_json
                
                if isinstance(collaborators_list, list):
                    for collaborator_username in collaborators_list:
                        if verifyToken(token, collaborator_username):
                            can_save = True
                            break # Found a valid collaborator token
        
        if not can_save:
            return jsonify({
                "status": "error",
                "message": "Invalid token or insufficient permissions to save."
            }), 403
        
        # Step 4: Update project timestamp in the database
        update_query = "UPDATE projects SET EditTS = CURRENT_TIMESTAMP WHERE projectID = %s"
        cursor.execute(update_query, (project_id,))
        db_connection.commit()

        # Step 5: Call internalSaveProject to write data to file
        return internalSaveProject(request, project_id) 
    
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500
    finally:
        cursor.close()
        db_connection.close()


