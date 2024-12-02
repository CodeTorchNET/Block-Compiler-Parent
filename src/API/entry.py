from flask import Flask
from internalAPI import register_routes

app = Flask(__name__)

@app.route("/")
def hello_world():
    return "<p>CodeTorch Block Compiler API is running.</p>"

register_routes(app)

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)