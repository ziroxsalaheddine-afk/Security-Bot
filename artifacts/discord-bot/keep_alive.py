import threading
from flask import Flask

app = Flask(__name__)


@app.route("/")
def home():
    return "Guardian Bot is alive!", 200


def keep_alive():
    thread = threading.Thread(target=lambda: app.run(host="0.0.0.0", port=8888))
    thread.daemon = True
    thread.start()
