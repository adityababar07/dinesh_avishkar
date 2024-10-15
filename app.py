from flask import Flask, render_template, jsonify, request
import socket

app = Flask(__name__)

# TCP server details
TCP_IP = '192.168.101.98' 
TCP_PORT = 40674  


def send_tcp_message(message):
    """Send a TCP message to the server."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((TCP_IP, TCP_PORT))
        s.sendall(message.encode())
        print(message)
        data = s.recv(1024)
        print(f"Received {data!r}")
        s.close()
    except Exception as e:
        print(f"Error sending TCP message: {e}")

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/toggle', methods=['POST'])
def toggle():
    state = request.json['state']
    if state:
        send_tcp_message("Switch ON")
    else:
        send_tcp_message("Switch OFF")
    return jsonify({"status": "Message sent"})

if __name__ == '__main__':
    app.run(debug=True)