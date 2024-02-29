#!/usr/bin/env python3
# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
"""
------------------------------------------------------------------------

 Description:
  Lookup query against TIDE active state and historic data
  producing basic report on whether there is active/historical
  data with simplified report output.

  For more extensive output for a specific IOC use tide-lookup.py

 Requirements:
  Requires bloxone, tqdm

 Usage:
    simple_tide_report.py [options] <query>
        -h  help
        -c  <file> inifile location (default ./config.ini)
        -i  <file> Input file (one IOC per line)
        -o  <file> CSV Output file for results
        -b  <file> File for reporting of bogus lines
        -a  active threats only
        -l  local database (activeonly)
        -d  debug output

 Author: Chris Marrison

 Date Last Updated: 20240228

 .. todo::
    * Option to treat URLs as hostnames i.e. parse URL to extract host
    * Considering adding option to do DNS lookup on hosts and TIDE lookups
      on returned CNAMEs and IPs, however, may need different data structure

Copyright 2019 - 2023 Chris Marrison / Infoblox

Redistribution and use in source and binary forms,
with or without modification, are permitted provided
that the following conditions are met:

1. Redistributions of source code must retain the above copyright
notice, this list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright
notice, this list of conditions and the following disclaimer in the
documentation and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
"AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
POSSIBILITY OF SUCH DAMAGE.

------------------------------------------------------------------------
"""
__version__ = '3.2.2'
__author__ = 'Chris Marrison'

import bloxone
import sys
import os
import shutil
import datetime
import configparser
import argparse
import collections
import requests
import json
import logging
import tqdm

# ** Global Variables **
log = logging.getLogger(__name__)

# ** Functions **


def parseargs():
    '''
    Parse Arguments Using argparse

    Parameters:
        None

    Returns:
        Returns parsed arguments
    '''
    parse = argparse.ArgumentParser(description='TIDE reporting tool with '
                                    'simplified CSV output and statistics')
    parse.add_argument('-i', '--input', type=str, required=True,
                       help="Input filename")
    parse.add_argument('-o', '--output', type=str,
                       help="CSV Output to <filename>")
    parse.add_argument('-b', '--bogus', type=str,
                       help="Output invalid lines to file")
    parse.add_argument('-c', '--config', type=str, default='config.ini',
                       help="Overide Config file")
    parse.add_argument('-a', '--active', action='store_true',
                       help="Process active only")
    parse.add_argument('-C', '--check_domains', action='store_true',
                       help="Check domain in addition to fqdn (hosts only)")
    parse.add_argument('-w', '--webcat', action='store_true',
                       help="Add Infoblox Web Categorisation Data (hosts only)")
    parse.add_argument('-l', '--local', type=str,
                       help="Use local database <filename>")
    parse.add_argument('-d', '--debug', action='store_true',
                       help="Enable debug messages")

    return parse.parse_args()


def setup_logging(debug):
    '''
     Set up logging

     Parameters:
        debug (bool): True or False.

     Returns:
        None.

    '''
    # Set debug level
    if debug:
        logging.basicConfig(level=logging.DEBUG,
                            format='%(asctime)s %(levelname)s: %(message)s')
    else:
        logging.basicConfig(level=logging.INFO,
                            format='%(asctime)s %(levelname)s: %(message)s')

    # Create logger and add Console handler
    # log = logging.getLogger(__name__)
    # log.addHandler(fileh)
    # log.addHandler(console)
    return


def open_file(filename):
    '''
     Attempt to open output file

     Parameters:
        filename (str): Name of file to open.

     Returns:
        file handler object.

    '''
    if os.path.isfile(filename):
        backup = filename+".bak"
        try:
            shutil.move(filename, backup)
            log.info("Outfile exists moved to {}".format(backup))
            try:
                handler = open(filename, mode='w')
                log.info("Successfully opened output file {}."
                         .format(filename))
            except IOError as err:
                log.error("{}".format(err))
                handler = False
        except IOError:
            log.warning("Could not backup existing file {}."
                        .format(filename))
            handler = False
    else:
        try:
            handler = open(filename, mode='w')
            log.info("Successfully opened output file {}.".format(filename))
        except IOError as err:
            log.error("{}".format(err))
            handler = False

    return handler


def output_bogus(data, file, line_number):
    '''
    Write invalid data to file

    Parameters:
        data (str): data to Write
        file (filehandle): file handler
        line_number (int): bogus line number

    Returns:
        no data.

    '''
    file.write(str(line_number)+":   "+data+"\n")
    return


def output_counter(cc):
    '''
    Output all entries in a counter by value

    Parameters:
        cc is a collection.Counter() obj.

    Returns:
        No data.

    '''
    for key in cc.items():
        print('  {}: {}'.format(key[0], key[1]))
    return


def getkeys(cc):
    '''
    Get keys from collections.Counter object

    Parameters:
        cc: collection.counter object

    Returns:
        keys: List of keys in counter

    '''
    keys = []
    for item in cc.items():
        keys.append(item[0])

    return keys


def most_recent(t1, t2):
    '''
    Compare two datetime stamps and return most recent

    Parameters:
        t1: timestamp
        t2: timestamp

    Returns:
        mostrecent: most recent timestamp from t1,t2.

    '''
    if t1 > t2:
        mostrecent = t1
    else:
        mostrecent = t2
    return mostrecent


def checkactive(query, qtype, b1td, check_domain=False, domain_checked=False):
    '''
    Check for active threat intel, parse and output results

    Parameters:
        query (str): hostname, ip, or url
        qtype (str): query type (host, ip, url)
        b1td (obj): Instantiated bloxone.b1td class

    Returns:
        totalthreats (int): number of active threats Found
        profiles (list): TIDE Profiles
        tclasses (list): List of threat classes

    '''
    # Set up local counters
    totalthreats = 0
    profile_stats = collections.Counter()
    class_stats = collections.Counter()
    # threat_types = collections.Counter()
    # property_stats = collections.Counter()
    profiles = []
    tclasses = []
    results = [ totalthreats, profiles, tclasses, domain_checked ]

    # Query active TIDE data
    response = b1td.querytidestate(qtype, query)
    # Process Response
    if response.status_code in b1td.return_codes_ok:
        # Parse json
        parsed_json = json.loads(response.text)

        log.debug('Quey: {}, Query type: {}'.format(query, qtype))
        log.debug('Raw response: {}'.format(response.text))

        # Parse Results
        # Check for threat construct
        if parsed_json.get('threat'):
            for threat in parsed_json['threat']:
                # Collect stats
                totalthreats += 1
                profile_stats[threat['profile']] += 1
                class_stats[threat['class']] += 1
                # threat_types[threat['type']] += 1
                # property_stats[threat['property']] += 1

            # Generate basic output
            profiles = getkeys(profile_stats)
            tclasses = getkeys(class_stats)
            log.debug('{}, {} active threat(s) {}'.format(query, totalthreats,
                                                          profiles))
            results = [ totalthreats, profiles, tclasses, domain_checked ]
        else:
            log.debug('{}, No active threats found'.format(query))
            if check_domain:
                domain = bloxone.utils.strip_host(query)
                if not domain == query:
                    results = checkactive(domain, 
                                          qtype, 
                                          b1td, 
                                          check_domain=False,
                                          domain_checked=True)

    else:
        log.error("Query Failed with response: {}".format(response.status_code))
        log.error("Body response: {}".format(response.text))
        totalthreats = -1
        profiles = "API Exception Occurred"
        results = [ totalthreats, profiles, tclasses, domain_checked ]

    return results


def checktide(query, qtype, b1td, check_domain=False, domain_checked=False):
    '''
    Check against all threat intel data, parse and output results

    Parameters:
        query (str): hostname, ip, or url
        qtype (str): query type (host, ip, url)
        b1td (obj): Instantiated bloxone.b1td class

    Returns:
        totalthreats (int): number of active threats Found
        profiles (list): TIDE Profiles
        tclasses (list): List of threat classes
        last_available (datetime): Most recent 'available' date if available
        last_expiration (datetime): Most recent 'expiration' date if available

    '''
    # Set up local counters
    totalthreats = 0
    profile_stats = collections.Counter()
    class_stats = collections.Counter()
    last_available = datetime.datetime.fromtimestamp(0)
    last_expiration = datetime.datetime.fromtimestamp(0)
    # threat_types = collections.Counter()
    # property_stats = collections.Counter()
    profiles = []
    tclasses = []
    results = [ totalthreats, profiles, tclasses, 
                last_available, last_expiration, domain_checked ]

    # Query TIDE (complete)
    response = b1td.querytide(qtype, query)

    # Process response
    if response.status_code in b1td.return_codes_ok:

        # Parse json
        parsed_json = json.loads(response.text)

        log.debug('Quey: {}, Query type: {}'.format(query, qtype))
        log.debug('Raw response: {}'.format(response.text))

        # Check for threat construct
        if parsed_json.get('threat'):
            for threat in parsed_json['threat']:
                # Collect stats
                totalthreats += 1
                profile_stats[threat['profile']] += 1
                class_stats[threat['class']] += 1
                # threat_types[threat['type']] += 1
                # property_stats[threat['property']] += 1

                # Collect available/expiration dates, determine most recent
                available = datetime.datetime.strptime(threat['imported'],
                                                       '%Y-%m-%dT%H:%M:%S.%fZ')
                last_available = most_recent(last_available, available)
                if "expiration" in threat.keys():
                    expiration = datetime.datetime.strptime(
                        threat['expiration'], '%Y-%m-%dT%H:%M:%S.%fZ')
                    last_expiration = most_recent(last_expiration, expiration)

            # Add basic output
            profiles = getkeys(profile_stats)
            tclasses = getkeys(class_stats)
            log.debug('{}, {} total item(s) of threat intel {}, {}'
                      .format(query, totalthreats, profiles, tclasses))
        else:
            log.debug('{}, No threat intel found'.format(query))
            if check_domain:
                domain = bloxone.utils.strip_host(query)
                if not domain == query:
                    results = checktide(domain, 
                                        qtype, 
                                        b1td, 
                                        check_domain=False,
                                        domain_checked=True)
                    if results:
                        totalthreats = results[0]
                        profiles = results[1]
                        tclasses = results[2]
                        last_available = results[3]
                        last_expiration = results[4]
                        domain_checked = results[5]

    else:
        log.error("Query Failed with response: {}".format(response.status_code))
        log.error("Body response: {}".format(response.text))
        totalthreats = -1
        profiles = "API Exception Occurred"

    # Check whether dates were updated
    if last_available == datetime.datetime.fromtimestamp(0):
        last_available = ''
    if last_expiration == datetime.datetime.fromtimestamp(0):
        last_expiration = ''

    results = [ totalthreats, profiles, tclasses, 
                last_available, last_expiration, domain_checked ]

    return results


def checkoffline(query, qtype, db_cursor, db_table):
    '''
     Check for active threat intel using local database

     Parameters:
        query (str): hostname, ip, or url
        qtype (str): query type (host, ip, url)
        db_cursor (db.cursor): database cursor
        db_table (db.table): database table

     Returns:
        totalthreats (int): number of active threats Found
        profiles (list): TIDE Profiles
        tclasses (list): List of threat classes

    '''
    # Set up local counters
    totalthreats = 0
    profile_stats = collections.Counter()
    class_stats = collections.Counter()
    # threat_types = collections.Counter()
    # property_stats = collections.Counter()
    profiles = []
    tclasses = []

    # Query active TIDE data
    rows = bloxone.utils.db_query(db_cursor, db_table, qtype, query)
    # Process Response

    log.debug('Quey: {}, Query type: {}'.format(query, qtype))
    log.debug('Raw response: {}'.format(rows))

    # Parse Results
    # Check for threat construct
    if rows:
        for row in rows:
            # Collect stats
            totalthreats += 1
            profile_stats[row['profile']] += 1
            class_stats[row['class']] += 1

        # Generate basic output
        profiles = getkeys(profile_stats)
        tclasses = getkeys(class_stats)
        log.debug('{}, {} active threat(s) {}'.format(query, totalthreats,
                                                      profiles))
    else:
        log.debug('{}, No active threats found'.format(query))

    return totalthreats, profiles, tclasses


def get_web_categories(query, qtype, b1td):
    '''
    '''
    web_categories = []
    response = b1td.dossierquery(query, 
                                    type=qtype, 
                                    sources="infoblox_web_cat")

    if response.status_code in b1td.return_codes_ok:
        results = response.json().get('results')
        data = results[0].get('data').get('results')
        if data:
            for cat in data:
                web_categories.append(cat.get('name'))
        else:
            web_categories = ['Uncategorised']
    
    else: 
        log.debug('{}, No active threats found'.format(query))
    
    return web_categories


def load_list(filename):
    '''
    '''
    items = []
    with open(filename) as file:
        for line in file:
            items.append(str(line.rstrip()))
    
    return items


def checkwebcat(webcats, block_list = []):
    '''
    '''
    result = False
    flag = False

    
    if webcats:
        for item in block_list:
            for cat in webcats:
                if item.lower() in cat.lower():
                    result = True
                    flag = True
                    break
            if flag:
                break
    
    return result


def checkcountry(domain, country_codes=['.ru', '.cn']):
    '''
    '''
    result = False

    if domain:
        for co in country_codes:
            if co in domain:
                result = True
                break
    
    return result


def gen_report(activethreats, totalthreats, web_categories, filehandle):
    '''
    Generate Threat Report

    Parameters:
        activethreats (dict): Dictionary of active threat query data
        totalthreats (dict): Dictionary of all threat query data
        filehandle (fh or None): file handler or None

    Returns:
        Report to STDOUT and/or file
        No return data

    '''
    # Local Variables
    total_hosts = len(activethreats)
    with_active = 0
    with_threats = 0
    action = ''
    block_categories = load_list('block_categories')
    country_codes = load_list('country_codes')

    # Parse items and output
    if len(totalthreats) == 0:
        # Assert Active Only
        # Check for CSV output
        if filehandle:
            # Write Header
            print('Host,Active Threats,Active Profiles,Active Classes',
                  file=filehandle)
        for item in activethreats:
            # Summarise Info
            # total_hosts += 1
            if activethreats[item][0] > 0:
                with_active += 1


            # File output
            if filehandle:
                print('{},{},"{}","{}"'.format(item, activethreats[item][0],
                                               activethreats[item][1],
                                               activethreats[item][2]),
                      file=filehandle)
            else:
                # Human Output
                print('Host: {}, Active threats: {}, Active profiles: {}, '
                    'Classes: {}' .format(item, activethreats[item][0],
                                            activethreats[item][1],
                                            activethreats[item][2]))

        # Add Summary
        # Human
        print('Summary: Total = {}, Active = {}, Not active = {}'
              .format(total_hosts, with_active, total_hosts - with_active))
        # File
        if filehandle:
            print('Summary: Total = {}, Active = {}, Not active = {}'
                  .format(total_hosts, with_active, total_hosts - with_active),
                  file=filehandle)

    else:
        # Assert active and total threats
        # Check for CSV output
        if filehandle:
            # Write Header
            print('Host,Action,Active Threats,Active Profiles,Total Indicators, '
                  'Indicator Profiles, Threat Classes,Last Seen,Last Expiry,'
                  'Domain Checked, Web Categories', file=filehandle)
        for item in activethreats:
            # Summarise Info
            # total_hosts += 1
            if activethreats[item][0] > 0:
                with_active += 1
            if totalthreats[item][0] > 0:
                with_threats += 1
            
            if activethreats[item][0] > 0:
                action = 'Active'
            elif totalthreats[item][0] > 0:
                action = 'Not Active'
            elif checkwebcat(web_categories.get(item), block_list=block_categories):
                action = 'Category Block'
            elif checkcountry(item, country_codes=country_codes):
                action = 'Country Block'
            else:
                action = ''

            # File output
            if filehandle:
                print(f'{item},{action},{activethreats[item][0]}, ' +
                      f'"{activethreats[item][1]}",{totalthreats[item][0]},' +
                      f'"{totalthreats[item][1]}","{totalthreats[item][2]}",' +
                      f'{totalthreats[item][3]},{totalthreats[item][4]},' +
                      f'{totalthreats[item][5]},"{web_categories.get(item)}"',
                      file=filehandle)
            else:
                # Human Output
                print('Host: {}, Action: {}, Active threats: {}, Active profiles: {}, '
                    'Total threats: {}, Profiles: {}, Classes: {}, '
                    'Last seen: {}, Last Expiry: {}, Domain Checked: {}, Web Categories: {}'
                    .format(item, action, activethreats[item][0], activethreats[item][1],
                            totalthreats[item][0], totalthreats[item][1],
                            totalthreats[item][2], totalthreats[item][3],
                            totalthreats[item][4], totalthreats[item][5],
                            web_categories.get(item)))

        # Add Summary
        # Human
        print('Summary: Total = {}, Active = {}, Threats = {}, No info = {}'
              .format(total_hosts, with_active, with_threats,
                      total_hosts - with_threats))
        # File
        if filehandle:
            print('Summary: Total = {}, Active = {}, Threats = {}, '
                  'No info = {}'.format(total_hosts, with_active, with_threats,
                                        total_hosts - with_threats),
                  file=filehandle)

    if filehandle:
        print('CSV output written to {}'.format(filehandle.name))

    return


def main(raw_args=None):
    '''
    * Main *

    Core logic when running as script

    '''

    # Local Variables
    exitcode = 0
    activethreats = {}
    totalthreats = {}
    domain_hit = []
    web_cats = {}
    # threats = 0
    linecount = 0
    total_lines = 0
    bogus_lines = 0
    # profiles = ''

    # Parse Arguments and configure
    args = parseargs(raw_args)

    # Set up logging
    debug = args.debug
    setup_logging(debug)

    # File Options
    bogusfilename = args.bogus
    inputfile = args.input
    outputfile = args.output
    if args.config:
        configfile = args.config
    else:
        configfile = 'config.ini'

    # Initialise bloxone
    b1td = bloxone.b1td(configfile)

    # General Options
    activeonly = args.active
    webcat = args.webcat
    check_domain = args.check_domains

    # Local database option
    if args.local:
        database = args.local
        # Force activeonly
        log.debug('Forcing check for active threats only.')
        activeonly = True
        log.debug('Opening local database {}'.format(database))
        db_cursor = bloxone.utils.opendb(database)
        if db_cursor:
            db_table = bloxone.utils.get_table(db_cursor)
            if not db_table:
                log.error('Local database table error, exiting.')
                sys.exit(1)
            else:
                log.info('Using local database for active lookups.')
                log.debug('Database opened successfully.')
        else:
            log.error('Local database error, exiting.')
            sys.exit(1)
    else:
        database = None

    # Set up output file for bogus lines
    if bogusfilename:
        bogus_out = open_file(bogusfilename)
        if not bogus_out:
            log.error('Failed to open output file for bogus lines.')
            exit(1)
    else:
        bogus_out = False

    # Set up output file for CSV
    if outputfile:
        outfile = open_file(outputfile)
        if not outfile:
            log.warning('Failed to open output file for CSV. Outputting to stdout only.')
    else:
        outfile = False

    # Build regexes for data_type checking
    host_regex, url_regex = bloxone.utils.buildregex()

    # Check for input file and attempt to read
    log.debug('Attempting to open input file: {}'.format(inputfile))
    try:
        # Open input file
        with open(inputfile) as file:
            # Determine number of lines
            for line in file:
                total_lines += 1
            # Return to start of file
            file.seek(0)
            log.debug('File {} opened, with {} lines'
                      .format(inputfile, total_lines))

            # Process file
            with tqdm.tqdm(total=total_lines) as pbar:
                for line in file:
                    query = str(line.rstrip())
                    linecount += 1
                    # Update progress bar
                    pbar.update(1)

                    # Get data type for query
                    qtype = bloxone.utils.data_type(query, host_regex, url_regex)

					# Process queries
                    # Check qtype and log bogus lines
                    log.debug("Query: {}".format(query))
                    log.debug("Data type of query: {}".format(qtype))

                    # Check qtype and log bogus lines
                    if qtype != "invalid":
                        # Determine TIDE or Local db
                        if database:
                            # Use local database
                            log.debug("Querying local database.")
                            activethreats[query] = checkoffline(query,
                                                                qtype,
                                                                db_cursor,
                                                                db_table)
                        else:
                            # Call checkactive
                            log.debug("Querying TIDE Active State Table...")
                            activethreats[query] = checkactive(query,
                                                            qtype,
                                                            b1td,
                                                            check_domain = check_domain)

                        # Call checktide
                        if not activeonly:
                            log.debug("Querying TIDE for all data...")
                            totalthreats[query] = checktide(query,
                                                            qtype,
                                                            b1td,
                                                            check_domain = check_domain)
                        
                        # Add web Category
                        if qtype == 'host' and webcat:
                            log.debug("Collecting web categorisation")
                            web_cats[query] = get_web_categories(query,
                                                            qtype,
                                                            b1td)

                    else:
                        # ASSERT: qtype == "invalid"
                        bogus_lines += 1
                        if bogus_out:
                            output_bogus(query, bogus_out, linecount)

        # Output Report
        gen_report(activethreats, totalthreats, web_cats, outfile)

    except IOError as error:
        log.error(error)
        exitcode = 1

    finally:
        # Close files
        if outfile:
            outfile.close()
        if bogus_out:
            bogus_out.close()

        log.debug("Processing complete.")

    return exitcode


# ** Main **
if __name__ == '__main__':
    exitcode = main()
    raise SystemExit(exitcode)

# ** End Main **
