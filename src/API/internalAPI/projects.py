from flask import jsonify

def projects_route():
    return jsonify({"message": "projects"})
