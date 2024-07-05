"""
main application
"""

from flask import Flask, redirect, url_for, request, session
from google.oauth2 import id_token
from google.auth.transport import requests


app = Flask(__name__)
app.secret_key = "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"
CLIENT_ID = "57950378250-95roigvlmlbj1ioknv2sge4ic5s28vlo.apps.googleusercontent.com"

# List of allowed users (replace with your actual user data)
ALLOWED_USERS = {"mark.veltzer@gmail.com"}

@app.route('/login', methods=['POST'])
def login():
    """ login endpoint """
    token = request.form['credential']
    try:
        idinfo = id_token.verify_oauth2_token(token, requests.Request(), CLIENT_ID)
        # Process user information from idinfo (email, etc.)
        session['user_id'] = idinfo['sub']  # Store user ID in session
        return redirect(url_for('index'))  # Redirect to the main page
    except ValueError:
        # Invalid token
        return "Invalid Token", 400

@app.route("/logout")
def logout():
    """ logout endpoint """
    if "user_id" in session:
        del session["user_id"]
    return redirect(url_for("/login"))
