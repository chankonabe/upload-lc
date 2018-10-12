#!/usr/bin/python

import argparse
import httplib
import json
import ntpath
import os
import random
import sys
import time

import httplib2
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from oauth2client.client import flow_from_clientsecrets
from oauth2client.file import Storage
from oauth2client import tools

###
# TWO REQUIRED FILES for this script to work
# client_secrets.json (format from Google)
# and
# configuration.json, which has attributes "logfile", a path+filename string to an upload log
# and "sourcedirs", a list of directories to upload from (the script has not been modified
# to take a dynamic length list of dirs, that should be one of the next fixes/updates
###k

# Explicitly tell the underlying HTTP transport library not to retry, since
# we are handling retry logic ourselves.
httplib2.RETRIES = 1

# Maximum number of times to retry before giving up.
MAX_RETRIES = 10

# Always retry when these exceptions are raised.
RETRIABLE_EXCEPTIONS = (httplib2.HttpLib2Error, IOError, httplib.NotConnected,
                        httplib.IncompleteRead, httplib.ImproperConnectionState,
                        httplib.CannotSendRequest, httplib.CannotSendHeader,
                        httplib.ResponseNotReady, httplib.BadStatusLine)

# Always retry when an apiclient.errors.HttpError with one of these status
# codes is raised.
RETRIABLE_STATUS_CODES = [500, 502, 503, 504]

# CLIENT_SECRETS_FILE, name of a file containing the OAuth 2.0 information for
# this application, including client_id and client_secret. You can acquire an
# ID/secret pair from the API Access tab on the Google APIs Console
#   http://code.google.com/apis/console#access
# For more information about using OAuth2 to access Google APIs, please visit:
#   https://developers.google.com/accounts/docs/OAuth2
# For more information about the client_secrets.json file format, please visit:
#   https://developers.google.com/api-client-library/python/guide/aaa_client_secrets
# Please ensure that you have enabled the YouTube Data API for your project.
CLIENT_SECRETS_FILE = "client_secrets.json"

YOUTUBE_SCOPES = "https://www.googleapis.com/auth/youtube.readonly https://www.googleapis.com/auth/youtube.upload"
YOUTUBE_API_SERVICE_NAME = "youtube"
YOUTUBE_API_VERSION = "v3"

# Helpful message to display if the CLIENT_SECRETS_FILE is missing.
MISSING_CLIENT_SECRETS_MESSAGE = """
WARNING: Please configure OAuth 2.0

To make this sample run you will need to populate the client_secrets.json file
found at:

   %s

with information from the APIs Console
https://code.google.com/apis/console#access

For more information about the client_secrets.json file format, please visit:
https://developers.google.com/api-client-library/python/guide/aaa_client_secrets
""" % os.path.abspath(os.path.join(os.path.dirname(__file__),
                                   CLIENT_SECRETS_FILE))


def get_authenticated_service():
    flow = flow_from_clientsecrets(CLIENT_SECRETS_FILE, scope=YOUTUBE_SCOPES,
                                   message=MISSING_CLIENT_SECRETS_MESSAGE)

    storage = Storage("%s-oauth2.json" % sys.argv[0])
    credentials = storage.get()

    if credentials is None or credentials.invalid:
        flags = tools.argparser.parse_args(args=[])
        credentials = tools.run_flow(flow, storage, flags)

    return build(YOUTUBE_API_SERVICE_NAME, YOUTUBE_API_VERSION,
                 http=credentials.authorize(httplib2.Http()))


def check_for_duplicate(options):
    youtube = get_authenticated_service()

    # Call the search.list method to see if video match comes up
    search_result = youtube.search().list(
        q=options["q"],
        part="id,snippet",
        forMine="true",
        type="video",
        maxResults=1
    ).execute()

    if search_result["pageInfo"]["totalResults"] == 0:
        return False

    return True


def initialize_upload(options):
    youtube = get_authenticated_service()

    tags = None
    if options["keywords"]:
        tags = options["keywords"].split(",")

    insert_request = youtube.videos().insert(
        part="snippet,status",
        body=dict(
            snippet=dict(
                title=options["title"],
                description=options["description"],
                tags=tags,
                categoryId=options["category"]
            ),
            status=dict(
                privacyStatus=options["privacyStatus"]
            )
        ),
        # chunksize=-1 means that the entire file will be uploaded in a single
        # HTTP request. (If the upload fails, it will still be retried where it
        # left off.) This is usually a best practice, but if you're using Python
        # older than 2.6 or if you're running on App Engine, you should set the
        # chunksize to something like 1024 * 1024 (1 megabyte).
        media_body=MediaFileUpload(options["file"], chunksize=-1, resumable=True)
    )

    resumable_upload(insert_request)


def resumable_upload(insert_request):
    response = None
    error = None
    retry = 0
    while response is None:
        try:
            print "Uploading file '%s'..." % upload_options["title"]
            status, response = insert_request.next_chunk()
            if 'id' in response:
                print "'%s' (video id: %s) was successfully uploaded." % (
                    upload_options["title"], response['id'])
            else:
                exit("The upload failed with an unexpected response: %s" % response)
        except HttpError, e:
            if e.resp.status in RETRIABLE_STATUS_CODES:
                error = "A retriable HTTP error %d occurred:\n%s" % (e.resp.status,
                                                                     e.content)
            else:
                raise
        except RETRIABLE_EXCEPTIONS, e:
            error = "A retriable error occurred: %s" % e

        if error is not None:
            print error
            retry += 1
            if retry > MAX_RETRIES:
                exit("No longer attempting to retry.")

            max_sleep = 2 ** retry
            sleep_seconds = random.random() * max_sleep
            print "Sleeping %f seconds and then retrying..." % sleep_seconds
            time.sleep(sleep_seconds)


def step(ext, dirname, names):

    for name in names:
        if name.lower().endswith(ext):
            pathfilelist.append(os.path.join(dirname, name))


if __name__ == '__main__':

    parser = argparse.ArgumentParser(description='Upload video to YouTube.')
    parser.add_argument("-c", "--config", help="JSON configuration file to override defaults.",
                        default="configuration.json")
    args = parser.parse_args()

    with open(args.config) as config_file:
        conf = json.load(config_file)

    try:
        # open logfile to read previous attempted uploads and write new ones
        with open(conf["logfile"], 'r') as f:
            previous_files = [line.strip() for line in f]
    except IOError:
        previous_files = []

    logfile = open(conf["logfile"], 'a')

    exten = ('.mov', '.mp4')
    pathfilelist = []

    for i in range(len(conf["sourcedirs"])):
        sourcepath = conf["sourcedirs"][i]

        print("Source Directory:" + sourcepath)

        # recursively traverse the current source directory and add to pathfilelist
        os.path.walk(sourcepath, step, exten)

        # filter out files that already exist in logfile
        newfilelist = [x for x in pathfilelist if x not in previous_files]

        for absolutepath in newfilelist:

            fname = os.path.basename(absolutepath)
            if os.path.isdir(absolutepath):
                # skip directories
                continue
            search_options = {"q": fname}

            # query YouTube search API to check if the filename being uploaded already exists in my channel
            if check_for_duplicate(search_options):
                print absolutepath + " is a duplicate, skipping!"
                # write to log file to ensure we don't try to upload on next script runk
                logfile.write(absolutepath + '\n')
                continue
            comparison_filename = fname.strip().lower()

            # make sure only MOVs or MP4s are uploaded
            if comparison_filename.startswith('.') or not comparison_filename.endswith(('.mov', '.mp4')):
                continue
            create_time = time.ctime(os.path.getmtime(absolutepath))
            upload_options = {"file": absolutepath, "title": ntpath.basename(absolutepath) + " - " + create_time,
                              "description": "Last modified on: " + create_time, "category": 22,
                              "keywords": "upload-lc",
                              "privacyStatus": "private"}
            if upload_options["file"] is None or not os.path.exists(upload_options["file"]):
                print "'%s' is not a valid file" % upload_options["file"]
            else:
                initialize_upload(upload_options)

            # write files from this path to directory after successful upload, so we don't attempt search query on them next time
            logfile.write(absolutepath + '\n')

    logfile.close()

    # with open(args.uploadlist) as f:
    #     for line_number, line in enumerate(f, 1):
    #         filename = line.strip()
    #         create_time = time.ctime(os.path.getmtime(filename))
    #         options = {"file": filename, "title": ntpath.basename(filename) + " - " + create_time,
    #                    "description": "Last modified on: " + create_time, "category": 22, "keywords": "test",
    #                    "privacyStatus": "private"}
    #         if options["file"] is None or not os.path.exists(options["file"]):
    #             print "'%s' is not a valid file" % options["file"]
    #         else:
    #             initialize_upload(options)
