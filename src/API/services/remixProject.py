from flask import jsonify
import mysql.connector
import json


from API.services.helpers import get_db_connection,limiter


def insert_new_project(cursor, is_shared, owner,title):
    """Inserts a new project into the database and returns the project ID."""
    insert_query = """
    INSERT INTO projects (isShared, Owner, Title)
    VALUES (%s, %s, %s)
    """
    try:
        cursor.execute(insert_query, (is_shared, owner, title))
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

def getUsernameFromToken(token):
    """Returns the username associated with the given token."""
    try:
        db_connection = get_db_connection("users")
        cursor = db_connection.cursor()
        cursor.execute("SELECT username FROM users WHERE AuthToken = %s", (token,))
        username = cursor.fetchone()
        return username[0] if username else None
    except mysql.connector.Error as err:
        raise Exception(f"Error getting username from token: {err}")
    finally:
        cursor.close()
        db_connection.close()

@limiter.limit("5 per minute")
def remixProject(request):
    """Handles the creation of a new project and stores data."""
    try:
        # Step 1: Establish DB connection
        db_connection = get_db_connection()
        cursor = db_connection.cursor()
        
        
        # Step 2: Insert remix into the database
        title = request.args.get('title')
        original_id = request.args.get('original_id')
        token = request.args.get('token')

        if(title == None or token == None):
            return jsonify({"status": "error", "message": "Missing username or token"}), 400

        # get the username from the token
        owner = getUsernameFromToken(token)
        if owner == None:
            return jsonify({"status": "error", "message": "Invalid token"}), 400

        # make sure the project we are remixing is shared
        cursor.execute("SELECT isShared FROM projects WHERE projectID = %s", (original_id,))
        is_shared = cursor.fetchone()
        if is_shared == None:
            return jsonify({"status": "error", "message": "Invalid project id"}), 400
        is_shared = is_shared[0]
        if is_shared == 0:
            return jsonify({"status": "error", "message": "Project is not shared"}), 400

        project_id = insert_new_project(cursor, 0, owner, title)

        # Step 3: Commit the transaction
        db_connection.commit()

        # Step 4: Save project data to a file
        if project_id:
            save_project_data_to_file(project_id, request.json)

            return jsonify({
                "status": "ok",
                "content-name": project_id,
                "content-title": title,
                "autosave-interval": "120"
            })

        else:
            return jsonify({"status": "error", "message": "Failed to remix project"})

    except Exception as err:
        print(f"Error: {err}")
        return jsonify({"status": "error", "message": str(err)}), 500

    finally:
        cursor.close()
        db_connection.close()
