from flask import jsonify
import mysql.connector
import json


from API.services.helpers import get_db_connection , verifyToken, limiter


def insert_new_project(cursor, is_shared, owner):
    """Inserts a new project into the database and returns the project ID."""
    insert_query = """
    INSERT INTO projects (isShared, Owner, Title)
    VALUES (%s, %s, %s)
    """
    try:
        cursor.execute(insert_query, (is_shared, owner, "Untitled"))
        return cursor.lastrowid
    except mysql.connector.Error as err:
        raise Exception(f"Error inserting new project: {err}")


def save_project_data_to_file(project_id, data):
    """Saves project data to a JSON file."""
    try:
        file_path = f'storage/projectData/projectData/{project_id}.json'
        with open(file_path, 'w') as file:
            file.write(json.dumps(data))
    except FileNotFoundError:
        raise Exception(f"Failed to create project file at {file_path}")

@limiter.limit("5 per minute")
def createNewProject(request):
    """Handles the creation of a new project and stores data."""
    try:
        # Step 1: Establish DB connection
        db_connection = get_db_connection()
        cursor = db_connection.cursor()
        
        
        # Step 2: Insert new project into the database
        is_shared = 0
        owner = request.args.get('username')
        token = request.args.get('token')

        if(owner == None or token == None):
            return jsonify({"status": "error", "message": "Missing username or token"}), 400

        if not verifyToken(token, owner):
            return jsonify({"status": "error", "message": "Invalid token"}), 403
    
        project_id = insert_new_project(cursor, is_shared, owner)

        # Step 3: Commit the transaction
        db_connection.commit()

        # Step 4: Save project data to a file
        if project_id:
            save_project_data_to_file(project_id, request.json)

            return jsonify({
                "status": "ok",
                "content-name": project_id,
                "content-title": "Untitled",
                "autosave-interval": "120"
            })

        else:
            return jsonify({"status": "error", "message": "Failed to insert new project"})

    except Exception as err:
        print(f"Error: {err}")
        return jsonify({"status": "error", "message": str(err)}), 500

    finally:
        cursor.close()
        db_connection.close()
