import json
import io
import zipfile
import time
import logging
import os
import pathlib
import tempfile
import subprocess

import flask
from flask import Flask, jsonify, request
from flask_cors import CORS
from sqlalchemy import create_engine, MetaData, Table
import sqlalchemy.engine.url
from sqlalchemy.orm import mapper, sessionmaker

from flask_sqlalchemy import SQLAlchemy

# side effect loads the env
import liwo_services
import liwo_services.export
import liwo_services.settings

logger = logging.getLogger(__name__)

def create_app_db():
    """load the dot env values"""
    liwo_services.settings.load_env()
    # Create the application instance
    app = Flask(__name__)
    # add db settings
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ['SQLALCHEMY_DATABASE_URI']
    app.config['DATA_DIR'] = os.environ['DATA_DIR']
    # add cors headers
    CORS(app)
    # load the database
    db = SQLAlchemy(app)
    logger.info("loaded database %s, files from %s", app.config['SQLALCHEMY_DATABASE_URI'], app.config['DATA_DIR'])
    return app, db


app, db = create_app_db()

# Create a URL route in our application for "/"
@app.route('/')
def home():
    """
    This function just responds to the browser ULR
    localhost:5000/

    :return:        the rendered template 'home.html'
    """
    return {'version': liwo_services.__version__}


@app.route('/liwo.ws/Authentication.asmx/Login', methods=["OPTIONS", "POST"])
def loadLayerSets():
    """
    returns maplayersets. Login is not used anymore, but frontend still expects this.
    frontend will send body {
    username: 'anonymous@rws.nl',
    password: '',
    mode: ''}

    TODO: remove Login part and only return json generated by postgresql function
    """


    rs = db.session.execute('SELECT website.sp_selectjson_maplayersets_groupedby_mapcategories()')

    result = rs.fetchall()

    layersets_dict = {
        "mode": "open",
        "layersets": result[0][0],
        "loggedIn": False,
        "liwokey": "-1",
        "error": "",
        "user": {
            "email": "",
            "message": "",
            "role": "Guest",
            "name": "",
            "organisation": "",
            "tools": [],
            "mymaps": [],
            "mapextent": "",
            "webserviceURL": "http://localhost:5000/liwo.ws/",
            "administrator": "false"
        }
    }

    layersets_string = json.dumps(layersets_dict)

    return {"d": layersets_string}

@app.route('/liwo.ws/Tools/FloodImage.asmx/GetScenariosPerBreachGeneric', methods=["POST"])
def loadBreachLayer():
    """
    Return Scenarios for a breachlocation.

    body: {
      breachid: breachId,
      layername: layerName
    })

     Based on layername a setname is defined.
     In the database function this is directly converted back to the layername.
     TODO: remove setname directly use layerName.
    """

    body = request.json

    # Set names according to c-sharp backend
    set_names = {
        "waterdiepte": "Waterdiepte_flood_scenario_set",
        "stroomsnelheid": "Stroomsnelheid_flood_scenario_set",
        "stijgsnelheid": "Stijgsnelheid_flood_scenario_set",
        "schade": "Schade_flood_scenario_set",
        "slachtoffers": "Slachtoffers_flood_scenario_set",
        "getroffenen": "Getroffenen_flood_scenario_set",
        "aankomsttijd": "Aankomsttijd_flood_scenario_set"
    }

    # Default value for setname
    default_set_name = "Waterdiepte_flood_scenario_set"
    set_name = set_names.get(body['layername'], default_set_name)
    breach_id = body['breachid']

    # define query with parameters
    query = "SELECT website.sp_selectjson_maplayerset_floodscen_breachlocation_id_generic(:breach_id, :set_name)"

    rs = db.session.execute(query, breach_id, set_name)
    result = rs.fetchall()
    return {"d": json.dumps(result[0][0])}


@app.route('/liwo.ws/Maps.asmx/GetLayerSet', methods=["POST"])
def loadLayerSetById():
    """
    body: { id }
    """
    body = request.json
    layerset_id = body['id']

    # TODO: use params option in execute.
    query = "SELECT website.sp_selectjson_layerset_layerset_id(:layerset_id)"

    rs = db.session.execute(query, layerset_id=layerset_id)
    result = rs.fetchall()
    return {"d": json.dumps(result[0][0])}

@app.route('/liwo.ws/Maps.asmx/GetBreachLocationId', methods=["POST"])
def getFeatureIdByScenarioId():
    """
    body:{ mapid: scenarioId }
    """
    body = request.json
    flood_simulation_id = body['floodsimulationid']

    # TODO: use params option in execute
    query = "SELECT static_information.sp_selectjson_breachlocationid(:flood_simulation_id)"

    rs = db.session.execute(query, flood_simulation_id=flood_simulation_id)
    result = rs.fetchall()

    return {"d": json.dumps(result[0][0])}


@app.route('/liwo.ws/Maps.asmx/DownloadZipFileDataLayers', methods=["POST"])
def download_zip():
    """
    body: {"layers":"scenario_18734,gebiedsindeling_doorbraaklocaties_buitendijks","name":"test"}
    """
    body = request.json
    layers = body.get('layers', '').split(',')
    layers_str = body.get('layers', '')
    name = body.get('name', '').strip()
    if not name:
        name = 'DownloadLIWO'

    data_dir = pathlib.Path(app.config['DATA_DIR'])


    # security check
    for layer in layers:
        if '..' in layer or layer.startswith('/'):
            raise ValueError('Security issue: layer name not valid')


    query = 'SELECT website.sp_select_filepaths_maplayers(:map_layers)'
    rs = db.session.execute(query, dict(map_layers=layers_str))
    # Results in the comma seperated list
    # [('static_information.tbl_breachlocations,shape1,static_information_geodata.infrastructuur_dijkringen,shape',)]
    result = rs.fetchall()

    # lookup relevant parts for cli script
    url = sqlalchemy.engine.url.make_url(app.config['SQLALCHEMY_DATABASE_URI'])

    # load datasets in a zip file
    zip_stream = liwo_services.export.add_result_to_zip(result, url, data_dir)

    resp = flask.send_file(
        zip_stream,
        mimetype='application/zip',
        attachment_filename='{}.zip'.format(name),
        as_attachment=True
    )
    return resp
