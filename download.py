#!/usr/bin/python3
# -*- coding: utf-8 -*-
# pylint: disable=missing-docstring, line-too-long

import json
import locale
import datetime
import xml.etree.ElementTree as ET
import os.path
import re
import argparse
import time

import requests
from prometheus_client import start_http_server
from prometheus_client.core import GaugeMetricFamily, REGISTRY

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:85.0) Gecko/20100101 Firefox/85.0',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'de,en-US;q=0.7,en;q=0.3',
    'Content-Type': 'application/json;charset=utf-8',
    'Origin': 'https://www.smard.de',
    'Connection': 'keep-alive',
    'Referer': 'https://www.smard.de/home/downloadcenter/download-marktdaten',
    'DNT': '1',
    'Sec-GPC': '1',
}

def round_time(dt=None, round_to=60):
    """Round a datetime object to any time lapse in seconds
    dt : datetime.datetime object, default now.
    round_to : Closest number of seconds to round to, default 1 minute.
    Author: Thierry Husson 2012 - Use it as you want but don't blame me.
    """
    if dt is None:
        dt = datetime.datetime.now()
    if isinstance(dt, int):
        dt = datetime.datetime.fromtimestamp(round(dt / 1000))
    seconds = (dt.replace(tzinfo=None) - dt.min).seconds
    rounding = (seconds+round_to/2) // round_to * round_to
    return dt + datetime.timedelta(0, rounding-seconds, -dt.microsecond)

FORM_DATA = {
    "request_form": [{
        "format": "XML",
        "moduleIds": [
            1001224,
            1004066,
            1004067,
            1004068,
            1001223,
            1004069,
            1004071,
            1004070,
            1001226,
            1001228,
            1001227,
            1001225,
            5000410,
            5004359
        ],
        "region":"DE",
        "timestamp_from": round(round_time(datetime.datetime.now(), 60*60).timestamp() - (15*60)) * 1000,
        "timestamp_to": round(round_time(datetime.datetime.now(), 60*60).timestamp()) * 1000,
        "type":"discrete",
        "language":"de"
    }]
}

'''
Module-Ids:

1001224: Realisierte Erzeugung > Kernenergie
1004066: Realisierte Erzeugung > Biomasse
1004067: Realisierte Erzeugung > Wind Onshore
1004068: Realisierte Erzeugung > Photovoltaik
1001223: Realisierte Erzeugung > Braunkohle
1004069: Realisierte Erzeugung > Steinkohle
1004071: Realisierte Erzeugung > Erdgas
1004070: Realisierte Erzeugung > Pumpspeicher
1001226: Realisierte Erzeugung > Wasserkraft
1001228: Realisierte Erzeugung > Sonstige Erneuerbare
1001227: Realisierte Erzeugung > Sonstige Konventionelle
1001225: Realisierte Erzeugung > Wind Offshore
5000410: Realisierter Stromverbrauch > Gesamt
5004359: Realisierter Stromverbrauch > Residuallast

31000714: Physikalischer Nettoexport (Grenzüberschreitend)
31000140: Luxemburg (Export)
31000569: Luxemburg (Import)
31000145: Schweiz (Export) 
31000574: Schweiz (Import)
31000141: Niederlande (Export)
31000570: Niederlande (Import)
31000139: Frankreich (Export)
31000568: Frankreich (Import)
31000138: Dänemark (Export)
31000567: Dänemark (Import)
31000146: Tschechien (Export)
31000575: Tschechien (Import)
31000144: Schweden (Export) 
31000573: Schweden (Import) 
31000142: Österreich (Export)
31000571: Österreich (Import)
31000143: Polen (Export)
31000572: Polen (Import)
'''
class CustomCollector:
    """
    Data Collector for serving them in prometheus client
    """

    def collect(self):
        """
        collectors only function called collect. and it collects data
        """
        global RESPONSE_DATA
        utc_distance = round(datetime.datetime.now().timestamp() - datetime.datetime.utcnow().timestamp())

        # utc_from = FORM_DATA['request_form'][0]['timestamp_from'] - (utc_distance * 1000) + 5400000

        # set timestamp artificially: 10minutes in the past
        utc_from = datetime.datetime.now().timestamp() - 10*60

        # from seconds to milliseconds
        utc_from = utc_from * 1000

        energy_data = GaugeMetricFamily('smard_energydata', 'consumption or production in KWh', labels=[
            'id', 'region', 'cat_name', 'module_name', 'energy_type'])
        for data in RESPONSE_DATA:
            energy_type = "unknown"
            if data['module_name'] == "Pumpspeicher":
                energy_type = "neutral"
            elif re.match("Wind Offshore|Wind Onshore|Wasserkraft|Sonstige Erneuerbare|Photovoltaik|Biomasse", data['module_name']) is not None:
                energy_type = "renewable"
            elif re.match("Kernenergie|Steinkohle|Braunkohle|Sonstige Konventionelle|Erdgas", data['module_name']) is not None:
                energy_type = "conventional"
            energy_data.add_metric(
                [str(data['id']), data['region'], data['category_name'], data['module_name'], energy_type], data['value'] * 1000, round(utc_from/1000))
        yield energy_data

ENDPOINT_URL = ''
CACHE_FILE = ''
RESPONSE_DATA = []

TS_NOW = round(round_time(datetime.datetime.now(), 24*3600).timestamp() * 1000)
PARSER = argparse.ArgumentParser(
    description='Convert data received from smard.de and provide them as prometheus-service')
PARSER.add_argument(
    '-a', '--api', default='https://www.smard.de/nip-download-manager/nip/download/market-data', help=r'api endpoint of mbroker')
PARSER.add_argument('-s', '--storage', default='downloads/',
                    help=r'store old data between calls e.g. to remember lastseen values')
PARSER.add_argument('-p', '--port', default=8000,
                    help=r'the port this service should listen to')
PARSER.add_argument('-m', '--modules', default='all',
                    help=r'ids of the modules to collect')
PARSER.add_argument('-d', '--dryrun', default=False, action='store_true',
                    help=r'dry run: just download, do not serve as server')
ARGS = PARSER.parse_args()

if ARGS.modules != 'all':
    if not ',' in ARGS.modules:
        single_module = int(ARGS.modules)
        FORM_DATA['request_form'][0]['moduleIds'] = [single_module]
        CACHE_FILE = ARGS.storage + str(single_module) + '.xml'
    else:
        list_modules_in = re.split(',', ARGS.modules)
        list_modules = []
        for module in list_modules_in:
            list_modules.append(int(module))
        list_modules.sort()
        print(list_modules)
        name = ''
        for modules in list_modules:
            if name != '':
                name += '-'
            name += str(modules)
        CACHE_FILE = ARGS.storage + name + '.xml'
        print(CACHE_FILE)

        FORM_DATA['request_form'][0]['moduleIds'] = list_modules
else:
    CACHE_FILE = ARGS.storage + 'all.xml'


def load():
    global TS_NOW, FORM_DATA, CACHE_FILE, HEADERS, RESPONSE_DATA
    TS_NOW = round(round_time(datetime.datetime.now(), 60*60).timestamp())

    # subtract 90minutes (younger data is mostly not available)
    TS_NOW -= 90*60

    FORM_DATA['request_form'][0]['timestamp_from'] = (TS_NOW - (900)) * 1000
    FORM_DATA['request_form'][0]['timestamp_to'] = (TS_NOW) * 1000

    # in case of the ex- and import-modulesids wee need a timeframe-size of one hour
    if len(FORM_DATA['request_form'][0]['moduleIds']) == 1 and  FORM_DATA['request_form'][0]['moduleIds'][0] >= 31000000 and FORM_DATA['request_form'][0]['moduleIds'][0] <= 31000999:
        TS_NOW = round(round_time(datetime.datetime.now(), 60*60).timestamp())
        TS_NOW -= 120*60
        FORM_DATA['request_form'][0]['timestamp_from'] = (TS_NOW - (60*60)) * 1000
        FORM_DATA['request_form'][0]['timestamp_to'] = (TS_NOW) * 1000

    print(FORM_DATA)


    if os.path.isfile(CACHE_FILE) and (datetime.datetime.now().timestamp() - os.path.getmtime(CACHE_FILE) < 900):
        filecontent = open(CACHE_FILE, 'r').read()
        root = ET.fromstring(filecontent)
    else:
        response = requests.post(
            ENDPOINT_URL, headers=HEADERS, cookies={}, data=json.dumps(FORM_DATA))
        with open(CACHE_FILE, 'wb') as output_file:
            output_file.write(response.content)
            print('-- Download Completed ---')
        root = ET.fromstring(response.content)

    RESPONSE_DATA = []
    locale.setlocale(locale.LC_NUMERIC, "de_DE.UTF-8")
    mod_index = 0
    for category in root.findall('kategorie'):
        cat_name = category.find('kategorie_name').text
        region_name = category.find('region').text
        modules = category.find('bausteine')
        for module in modules.findall('baustein'):
            mod_id = FORM_DATA['request_form'][0]['moduleIds'][mod_index]
            mod_index += 1
            module_dict = {'id': str(
                mod_id), 'region': region_name, 'category_name': '', 'module_name': '', 'value': 0, 'unit': ''}
            module_dict['category_name'] = cat_name
            module_name = module.find('baustein_name').text
            module_dict['module_name'] = module_name
            unit_name = module.find('einheit').text
            module_dict['unit'] = unit_name
            values = module.find('werte')
            sum_values = 0

            valid_sum = True
            for single_value in values.findall('wert_detail'):
                myval = single_value.find('wert')
                if myval is None:
                    myval = single_value.find('Value')
                if myval is not None and myval.text != '-':
                    sum_values += locale.atof(myval.text)
                else:
                    valid_sum = False
                
            module_dict['value'] = sum_values
            if valid_sum:
                RESPONSE_DATA.append(module_dict)

def main():
    """
    main function collecting data from input file/storage and serving prometheus data
    """
    global ENDPOINT_URL, CACHE_FILE, FORM_DATA, TS_NOW, ARGS

    ENDPOINT_URL = ARGS.api


if __name__ == '__main__':
    try:
        PORT_NUMBER = int(ARGS.port)
    except ValueError:
        exit('Error: ' + ARGS.port + ' is not a valid port-number')
    main()
    load()
    if ARGS.dryrun:
        exit()
    REGISTRY.register(CustomCollector())
    # Start up the server to expose the metrics.
    start_http_server(int(ARGS.port))
    # Generate some requests.
    while True:
        time.sleep(60*15)
        TS_NOW = round(round_time(
            datetime.datetime.now(), 15*60).timestamp())
        main()
        load()
