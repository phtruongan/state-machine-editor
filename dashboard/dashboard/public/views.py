# -*- coding: utf-8 -*-
"""Public section, including homepage and signup.
Need to run docker pull cmusatyalab/gabriel to pull down the image first
"""
import os
from subprocess import Popen, PIPE
import signal

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    url_for,
    current_app as app,
    send_from_directory,
)
from flask_login import login_required, login_user, logout_user
from werkzeug.utils import secure_filename

from dashboard.extensions import login_manager
from dashboard.public.forms import LoginForm, FileUploadForm
from dashboard.user.forms import RegisterForm
from dashboard.user.models import User
from dashboard.utils import flash_errors
from logzero import logger
import docker

client = docker.from_env()
blueprint = Blueprint("public", __name__, static_folder="../static")

GABRILE_CONTAINER_NAME = "gabriel-deploy"
GABRIEL_APP_CONTAINER_NAME = "gabriel-app"
app_proc = None


@blueprint.route("/uploads/<filename>")
def uploaded_file(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in app.config["ALLOWED_EXTENSIONS"]
    )


@login_manager.user_loader
def load_user(user_id):
    """Load user by ID."""
    return User.get_by_id(int(user_id))


def getContainerStatus(name):
    container = findContainer(name)
    container = checkContainer(container)
    if container != None:
        return container.status
    else:
        return "Not Running"


@blueprint.route("/", methods=["GET", "POST"])
def home():
    """Home page."""
    form = LoginForm(request.form)
    fileUploadForm = FileUploadForm()
    # Handle logging in
    if request.method == "POST":
        if request.form["btn"] == "registerForm":
            if form.validate_on_submit():
                login_user(form.user)
                flash("You are logged in.", "success")
                redirect_url = request.args.get("next") or url_for("user.members")
                return redirect(redirect_url)
            else:
                flash_errors(form)
        elif request.form["btn"] == "Upload":
            if fileUploadForm.validate_on_submit():
                f = fileUploadForm.mFile.data
                filename = secure_filename(f.filename)
                logger.debug("received uploaded file: {}".format(filename))
                if allowed_file(filename):
                    f.save(os.path.join(app.config["UPLOAD_FOLDER"], "app.pbfsm"))
                else:
                    flash("Invalid file format", "error")
    return render_template(
        "public/home.html",
        form=form,
        fileUploadForm=fileUploadForm,
        gabrielStatus=getContainerStatus(GABRILE_CONTAINER_NAME),
        appStatus=getAppStatus(),
    )


def getAppStatus():
    global app_proc
    if app_proc != None:
        if app_proc.poll() == None:
            return "Running"
    return "Not Running"


@blueprint.route("/logout/")
@login_required
def logout():
    """Logout."""
    logout_user()
    flash("You are logged out.", "info")
    return redirect(url_for("public.home"))


@blueprint.route("/register/", methods=["GET", "POST"])
def register():
    """Register new user."""
    form = RegisterForm(request.form)
    if form.validate_on_submit():
        User.create(
            username=form.username.data,
            email=form.email.data,
            password=form.password.data,
            active=True,
        )
        flash("Thank you for registering. You can now log in.", "success")
        return redirect(url_for("public.home"))
    else:
        flash_errors(form)
    return render_template("public/register.html", form=form)


@blueprint.route("/about/")
def about():
    """About page."""
    form = LoginForm(request.form)
    return render_template("public/about.html", form=form)


def findContainer(name):
    containers = client.containers.list()
    running = False
    for container in containers:
        if container.name == name:
            return container
    return None


def checkContainer(container):
    if container != None and container.status != "running":
        container.remove(force=True)
        return container
    else:
        return container


def startContainer():
    expose_ports = [8021, 9090, 9098, 9111, 10101, 10102, 10103, 10104, 10120, 22222]
    expose_port_dict = {
        "{}/tcp".format(port): ("0.0.0.0", port) for port in expose_ports
    }
    logger.debug("expose ports: {}".format(expose_port_dict))
    client.containers.run(
        "cmusatyalab/gabriel",
        '/bin/bash -c "gabriel-control -l -d -n eth0 & sleep 5; gabriel-ucomm -s 127.0.0.1:8021"',
        detach=True,
        auto_remove=True,
        name=GABRILE_CONTAINER_NAME,
        ports=expose_port_dict,
    )


@blueprint.route("/start/")
def start():
    logger.debug("Starting gabriel")
    container = findContainer(GABRILE_CONTAINER_NAME)
    container = checkContainer(container)
    if not container:
        startContainer()
    flash("Starting gabriel", "success")
    return redirect(url_for("public.home"))


@blueprint.route("/stop/")
def stop():
    logger.debug("Stopping gabriel")
    container = checkContainer(findContainer(GABRILE_CONTAINER_NAME))
    if container:
        container.remove(force=True)
    flash("Stopping gabriel", "success")
    return redirect(url_for("public.home"))


@blueprint.route("/startapp/")
def start_app():
    global app_proc
    script_path = os.path.join(
        os.path.dirname(os.path.realpath(__file__)), "run-gabriel-fsm.sh"
    )
    logger.debug("launching script: {}".format(script_path))
    if (app_proc == None) or (app_proc != None and app_proc.poll() != None):
        app_proc = Popen(["bash", script_path], stdout=PIPE, stderr=PIPE)
        flash("Started App", "success")
    else:
        flash("App is already running. Stop it before running another one.", "success")
    return redirect(url_for("public.home"))


@blueprint.route("/stopapp/")
def stop_app():
    global app_proc
    if app_proc:
        try:
            os.kill(app_proc.pid, signal.SIGTERM)
            status = app_proc.poll()
            logger.debug("child status after terminate: {}".format(status))
            if status == None:
                os.kill(app_proc.pid, signal.SIGKILL)
                logger.debug("child status: {}".format(app_proc.poll()))
        except ProcessLookupError as e:
            pass
        flash("App Stopped", "success")
    return redirect(url_for("public.home"))

