from flask import Flask, render_template, redirect, url_for
from google.appengine.api import users


app = Flask(__name__)

# List of allowed users (replace with your actual user data)
ALLOWED_USERS = {"mark.veltzer@gmail.com"}

@app.route("/")
def index():
    user = users.get_current_user()
    if user and user.email() in ALLOWED_USERS:
        return render_template("welcome.html", username=user.nickname())
    return redirect(users.create_login_url(url_for("index")))

@app.route("/logout")
def logout():
    return redirect(users.create_logout_url(url_for("index")))
