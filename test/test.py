from flask import request, Response, render_template, jsonify, Flask
import sys, os, json

app = Flask(__name__)
# app.config['SECRET_KEY'] = '9OLWxND4o83j4K4iuopO'

@app.route('/')
def index():
    return render_template('index.html')

if __name__ == "__main__":

    app.run(host='localhost',
            port='8081',
            debug='True',
            use_reloader=True)
    