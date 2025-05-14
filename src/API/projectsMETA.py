from flask import request, jsonify
import json
import uuid

from API.services.helpers import get_db_connection, verifyToken, limiter

def get_username_by_token(token):
    query = "SELECT username FROM users WHERE AuthToken = %s"
    db_connection = None
    try:
        db_connection = get_db_connection("users")
        with db_connection.cursor(dictionary=True) as cursor:
            cursor.execute(query, (token,))
            user_record = cursor.fetchone()
        # Ensure a dictionary is always returned, even if user_record is None
        return user_record if user_record else {"username": ""}
    except Exception as err:
        print(f"Error in get_username_by_token: {err}")
        return {"username": ""}
    finally:
        if db_connection:
            db_connection.close()


@limiter.limit("10 per minute")
def projectsMETA_route():
    if request.method == 'OPTIONS':
        return "", 204

    elif request.method == 'GET':
        project_id_str = request.args.get('id')
        user_access_token = request.args.get('token')

        if not project_id_str:
            return jsonify({"status": "error", "message": "Missing project ID"}), 400
        
        if not project_id_str.isdigit():
            return jsonify({"status": "error", "message": "Project ID must be a number"}), 400
        
        project_id = int(project_id_str)

        db_connection = None
        cursor = None

        try:
            db_connection = get_db_connection()
            cursor = db_connection.cursor(dictionary=True)

            # Step 2: Query the database for the project, including Collaborators and CRoomID
            query = "SELECT projectID, Title, Owner, isShared, Collaborators, CRoomID FROM projects WHERE projectID = %s"
            cursor.execute(query, (project_id,))
            project = cursor.fetchone()

            if not project:
                return jsonify({"status": "error", "message": "Invalid project ID"}), 404

            # Step 3: Determine current user and authorization
            # Use the username from the token if provided, otherwise it's an anonymous user
            current_username = ""
            if user_access_token:
                # get_username_by_token should return {'username': 'the_user'} or {'username': ''}
                user_data = get_username_by_token(user_access_token)
                current_username = user_data.get('username', "") # Safely get username

            can_access_private = False
            is_owner = current_username == project['Owner'] and current_username != ""
            
            collaborators_list = []
            if project['Collaborators']:
                if isinstance(project['Collaborators'], str):
                    try:
                        collaborators_list = json.loads(project['Collaborators'])
                    except json.JSONDecodeError:
                        print(f"Warning: Malformed JSON in Collaborators for projectID {project_id}")
                        pass # Treat as empty list
                elif isinstance(project['Collaborators'], list):
                    collaborators_list = project['Collaborators']
            
            is_collaborator = current_username in collaborators_list and current_username != ""

            if project['isShared'] == 1: # Public project
                can_access_private = True # Everyone can access public meta
            elif user_access_token:
                # Check if token is valid for owner or a collaborator for private projects
                if is_owner or is_collaborator:
                    can_access_private = True

            if not can_access_private:
                return jsonify({
                    "status": "error",
                    "message": "Access denied. Project is private or token is invalid/missing."
                }), 403

            # Step 4: Handle CRoomID and isCollaborative
            project_c_room_id = project['CRoomID']
            actual_collaborators_exist = bool(collaborators_list) # True if the list is not empty

            # Generate and save CRoomID if collaborators exist and CRoomID is NULL
            if actual_collaborators_exist and not project_c_room_id:
                project_c_room_id = uuid.uuid4().hex[:32] # Generate a 32-char ID
                try:
                    update_croom_query = "UPDATE projects SET CRoomID = %s WHERE projectID = %s"
                    cursor.execute(update_croom_query, (project_c_room_id, project_id))
                    db_connection.commit()
                except Exception as e_update:
                    db_connection.rollback()
                    print(f"Error updating CRoomID for project {project_id}: {e_update}")
                    project_c_room_id = None

            user_can_collaborate_on_this_project = False
            if project_c_room_id and (is_owner or is_collaborator):
                user_can_collaborate_on_this_project = True


            # Step 5: Prepare metadata
            can_save = "false"
            if is_owner or is_collaborator:
                can_save = "true"

            can_remix = "false"

            if project['isShared'] == 1 and current_username and not is_owner and not is_collaborator:
                can_remix = "true"
            
            metadata = {
                "id": project['projectID'],
                "title": project['Title'],
                "visibility": "true" if project['isShared'] == 1 else "false",
                "author": {
                    "username": project['Owner'],
                    "history": {"joined": "1900-01-01T00:00:00.000Z"},
                },
                "username": current_username,
                "canSave": can_save,
                "canRemix": can_remix,
                "isCollaborative": "true" if user_can_collaborate_on_this_project else "false",
                "collaboratorRoom": project_c_room_id if user_can_collaborate_on_this_project else "false",
            }
            return jsonify(metadata), 200

        except Exception as err:
            if db_connection and 'db_connection.rollback' in dir(db_connection):
                db_connection.rollback()
            print(f"Unexpected error in projectsMETA_route for projectID {project_id_str}: {err}")
            return jsonify({"status": "error", "message": "An unexpected server error occurred."}), 500
        finally:
            if cursor:
                cursor.close()
            if db_connection:
                db_connection.close()
    else:
        return jsonify({"status": "error", "message": "Method not supported"}), 405