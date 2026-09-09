#! /usr/bin/env python

import sys, logging, time, os, datetime, json, threading, socket, traceback
from pprint import pprint
from collections import defaultdict
from dataclasses import dataclass, asdict
import uuid

from flask import Flask, request, Response, redirect, url_for, jsonify
import flask.cli

import redis
import requests # pip install requests

import schedule # pip install schedule
from schedule import every, repeat

# Prometheus
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST, Gauge, Counter

# silence Flask output messages
flask.cli.show_server_banner = lambda *args, **kwargs: None
logging.getLogger('werkzeug').disabled = True # disable wsgi logging

# ENV_VARS - config vars
ISS_CHECK_INTERVAL = os.getenv('ISS_CHECK_INTERVAL', 10)
ISS_CHECK_URL = os.getenv('ISS_CHECK_URL', "")
ISS_REDIS_HOST = os.getenv('ISS_REDIS_HOST', "")
ISS_REDIS_PORT = os.getenv('ISS_REDIS_PORT', "")
ISS_CACHE_KEY = os.getenv('ISS_CACHE_KEY', "")
ISS_CACHE_TTL = os.getenv('ISS_CACHE_TTL', "")

# Prometheus - Metric Vars
FLASK_REQUEST_COUNT = Counter('flask_request_count', 'Flask Request Count', ['method', 'endpoint', 'http_status'])
ISS_LATITUDE = Gauge('iss_latitude', 'ISS latitude')
ISS_LONGITUDE = Gauge('iss_longitude', 'ISS longitude')
ISS_CHECKS = Counter('iss_checks_total', 'Total ISS checks')

#
def get_now():
	return datetime.datetime.now().strftime("%Y:%m:%d %H:%M:%S.%f")

def get_epoch():
	return time.time()

def get_epoch_nanoseconds():
	return time.time_ns()

def get_pid():
	return os.getpid()

def get_hostname():
	return socket.gethostname()

def get_app_uptime(app_start_time):
    secs = get_epoch() - app_start_time
    result = datetime.timedelta(seconds=secs)
    return str(result)

def get_datetime_from_epoch(seconds):
    return datetime.datetime.fromtimestamp(seconds).strftime("%Y-%m-%d %H:%M:%S")

def log(message, level="INFO", **extra):
	out = {"timestamp": get_now(), "epoch": get_epoch(), "pid": _PID, "level": level, "message": message}
	if extra: out |= extra
	print(json.dumps(out), flush=True)
	return True

##
app = Flask(__name__)
APP_NAME = os.getenv('APP_NAME', "CHANGEME!")
APP_PORT = os.getenv('APP_PORT', 9999)
__VERSION__ = get_datetime_from_epoch(os.path.getmtime(os.path.basename(__file__)))
__BUILD__ = os.path.getmtime(os.path.basename(__file__))
_APP_START_TIME = get_epoch()
_PID = get_pid()
_HOSTNAME = get_hostname()

redis_client = redis.Redis(host=ISS_REDIS_HOST, port=ISS_REDIS_PORT, decode_responses=True)

###
@dataclass()
class Stats:
	last_run: int
	last_run_ts: str
	next_run_ts: str
	next_run_seconds: int
	iss_check_interval: int
	total_runs: int

stats = Stats(0, "", "", 0, ISS_CHECK_INTERVAL, 0)


def redis_status():
	redis_ping = redis_client.ping()
	log("Redis PING", "DEBUG", response=str(redis_ping))
	redis_info = redis_client.info()
	log("Redis INFO", "DEBUG", response=json.dumps(redis_info))


def get_iss_position_from_cache():

	log("Attempt to fetch ISS data from Cache.", "DEBUG")

	cached_position = redis_client.get(ISS_CACHE_KEY)

	if cached_position:
		log("[Cache OK] Fetching ISS location from Redis.", "DEBUG")
		return json.loads(cached_position)

	else:
		log("[Cache NOK]", "DEBUG")

		# call API to get ISS position
		api_data = get_iss_position_from_api()

		# store ISS position data in Key/Cache
		redis_client.set(name=ISS_CACHE_KEY, value=json.dumps(api_data), ex=ISS_CACHE_TTL, nx=True)
		
		log("Successfully CACHED ISS data.", "DEBUG")

		return api_data


def get_iss_position_from_api():

	log("Fetching fresh ISS data from API.", "DEBUG")

	try:

		response = requests.get(ISS_CHECK_URL, timeout=10)
		response.raise_for_status()
		iss_api_data = response.json()
		log("ISS API Response JSON.", "DEBUG", iss_api_data=iss_api_data)
		return iss_api_data
	
	except requests.exceptions.RequestException as e:
			log(f"API Request failed: {e}", "ERROR")
			return None


def iss_check(run_id):

	try:
		stats.total_runs += 1

		log("ISS Check.", run_id=run_id)

		iss_data = get_iss_position_from_cache()

		if iss_data and iss_data.get("message") == "success":
			pos = iss_data["iss_position"]
			timestamp = iss_data["timestamp"]
			log(f"ISS Position at timestamp {timestamp}: Lat {pos['latitude']}, Lon {pos['longitude']}", "DEBUG")

			# Prometheus updates
			ISS_CHECKS.inc()
			ISS_LATITUDE.set(pos['latitude'])
			ISS_LONGITUDE.set(pos['longitude'])

		else:
			log("Could not retrieve ISS position data.", "WARN")

		stats.last_run = get_epoch()
		stats.last_run_ts = get_now()

	except Exception as e:
		log("iss_check()", "ERROR", traceback=traceback.format_exc())


def run():

	log("Starting Scheduler")

	schedule.every(int(ISS_CHECK_INTERVAL)).minutes.do(lambda: iss_check(uuid.uuid4().hex)).tag('iss')

	while True:
		schedule.run_pending()
		time.sleep(1)
		stats.next_run_seconds = schedule.idle_seconds()
		stats.next_run_ts = str(schedule.next_run())


@repeat(every(1).hour.at(":00"))
def log_checkpoint():
	log("Checkpoint!", "DEBUG")


@app.after_request
def after_request(response):
	FLASK_REQUEST_COUNT.labels(request.method, request.path, response.status_code).inc()
	return response


@app.route('/', methods=['GET'])
def index():
	return redirect(url_for('info')) # redirect example


@app.route("/stats")
def stats_info():
	data = {"stats": asdict(stats)}
	log("/stats", "DEBUG")
	return jsonify(data), 200


@app.route('/info')
def info():
	data = { 
		"hostname": _HOSTNAME, 
		"app-name": APP_NAME, 
		"app-start-time": _APP_START_TIME, 
		"app-uptime": get_app_uptime(_APP_START_TIME),
		"pid": _PID, "request-ts": get_epoch(), 
		"request-ns": get_epoch_nanoseconds(), 
		"version": __VERSION__, 
		"build": __BUILD__ }
	log("/info", "DEBUG")
	return jsonify(data), 200


@app.route('/metrics')
def metrics():
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST) # Return formatted metrics text to Prometheus


if __name__ == '__main__':

	log(f"*** {APP_NAME} started ***", host=_HOSTNAME, appname=APP_NAME, appstarttime=_APP_START_TIME, port=APP_PORT)

	redis_status()

	log("Starting [Scheduler] Thread")
	thread = threading.Thread(target=run)
	thread.start()

	app.run(host='0.0.0.0', port=APP_PORT, debug=False)
