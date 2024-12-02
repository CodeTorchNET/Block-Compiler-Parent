from flask import jsonify

def assets_route():
    return jsonify({"data": "assets"})
