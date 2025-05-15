from flask import request, jsonify
import os
from datetime import timezone

from API.services.helpers import get_db_connection


def LP_routes():
    # Handle preflight request
    if request.method == 'OPTIONS':
        return ''
    
    # Validate the token
    token = request.json.get('token')
    if token != os.getenv("InternalAPIKey"):
        return jsonify({"error": "Invalid token"}), 401
    
    # Extract data from request
    username = request.json.get('username')

    # Step 1: Establish DB connection
    db_connection = get_db_connection("projects")
    cursor = db_connection.cursor(dictionary=True)

    # Step 2: Get user's projects sorted by timestamp
    query = """
        SELECT projectID, Title, Owner, EditTS, Collaborators
        FROM projects
        WHERE Owner = %s 
            OR (Collaborators IS NOT NULL AND JSON_CONTAINS(Collaborators, JSON_QUOTE(%s), '$'))
        ORDER BY EditTS DESC
    """
    cursor.execute(query, (username, username))
    projects = cursor.fetchall()
    project_data = [{
        "projectID": project['projectID'],
        "EditTS": project['EditTS'].astimezone(timezone.utc).isoformat()
    } for project in projects]
    return jsonify({"status": "true", "projects": project_data})