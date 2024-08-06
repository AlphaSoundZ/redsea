#!/usr/bin/env python

from flask import Flask, request, jsonify
import os
import re
import sys
import traceback
import urllib3

import redsea.cli as cli
from redsea.mediadownloader import MediaDownloader
from redsea.tagger import Tagger
from redsea.tidal_api import TidalApi, TidalError
from redsea.sessions import RedseaSessionFile
from config.settings import PRESETS, BRUTEFORCEREGION

app = Flask(__name__)

# Preload and disable warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
os.chdir(sys.path[0])

# Constants
LOGO = """ ... """  # your logo here

MEDIA_TYPES = {'t': 'track', 'p': 'playlist', 'a': 'album', 'r': 'artist', 'v': 'video'}

# Flask Routes

@app.route('/')
def index():
    return LOGO

@app.route('/id/<string:media_id>')
def get_media_by_id(media_id):
    try:
        RSF = RedseaSessionFile('./config/sessions.pk')
        preset = PRESETS['default']  # Use the default preset or modify as needed
        preset['quality'] = []
        preset['quality'].append('HI_RES') if preset['MQA_FLAC_24'] else None
        preset['quality'].append('LOSSLESS') if preset['FLAC_16'] else None
        preset['quality'].append('HIGH') if preset['AAC_320'] else None
        preset['quality'].append('LOW') if preset['AAC_96'] else None

        md = MediaDownloader(TidalApi(RSF.load_session('TV')), preset, Tagger(preset))

        # Determine type
        type = None
        if media_id.isdigit():
            type = md.type_from_id(media_id)
        else:
            pattern = re.compile('^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')
            if pattern.match(media_id):
                try:
                    md.playlist_from_id(media_id)
                    type = 'p'
                except TidalError:
                    return "The playlist id could not be found!", 404

        if not type:
            return "The id is not valid.", 400

        media_to_download = [{'id': media_id, 'type': type}]

        # Download
        result = download_media(media_to_download, RSF, preset)

        return jsonify({ 'directory': result})

    except Exception as e:
        return str(e), 500

def download_media(media_to_download, RSF, preset):
    download_directory = ""
    for mt in media_to_download:
        if not mt['type'] in MEDIA_TYPES:
            continue

        md = MediaDownloader(TidalApi(RSF.load_session('TV')), preset.copy(), Tagger(preset))
        session_gen = RSF.get_session()

        def get_tracks(media):
            media_name = None
            tracks = []
            media_info = None
            track_info = []

            while True:
                try:
                    if media['type'] == 't':
                        tracks.append(md.api.get_track(media['id']))
                    elif media['type'] == 'p':
                        playlist = md.api.get_playlist(media['id'])
                        playlist_items = md.api.get_playlist_items(media['id'])['items']
                        for item_ in playlist_items:
                            tracks.append(item_['item'])
                    elif media['type'] == 'a':
                        media_info = md.api.get_album(media['id'])
                        tracks = md.api.get_album_tracks(media['id'])['items']
                    elif media['type'] == 'v':
                        tracks.append(md.api.get_video(media['id']))
                    else:
                        media_name = md.api.get_artist(media['id'])['name']
                        albums = md.api.get_artist_albums(media['id'])['items'] + md.api.get_artist_albums_ep_singles(media['id'])['items']
                        for album in albums:
                            media_info = md.api.get_album(album['id'])
                            tracks = md.api.get_album_tracks(album['id'])['items']
                            track_info.append((tracks, media_info))
                        if not track_info:
                            track_info = [(tracks, media_info)]
                        return media_name, track_info

                    if not track_info:
                        track_info = [(tracks, media_info)]
                    return media_name, track_info

                except TidalError as e:
                    if 'not found. This might be region-locked.' in str(e) and BRUTEFORCEREGION:
                        try:
                            session, name = next(session_gen)
                            md.api = TidalApi(session)
                            continue
                        except StopIteration as s:
                            raise s
                    else:
                        raise(e)

        try:
            media_name, track_info = get_tracks(media=mt)
        except StopIteration:
            continue

        total = sum([len(t[0]) for t in track_info])
        cur = 0
        for tracks, media_info in track_info:
            for track in tracks:
                first = True
                while True:
                    try:
                        album_location, temp_file = md.download_media(track, media_info, overwrite=False, track_num=cur+1 if mt['type'] == 'p' else None)
                        download_directory = album_location
                        break
                    except (ValueError, OSError, AssertionError) as e:
                        if 'Unable to download track' in str(e) and BRUTEFORCEREGION:
                            try:
                                if first:
                                    session_gen = RSF.get_session()
                                    first = False
                                session, name = next(session_gen)
                                md.api = TidalApi(session)
                                continue
                            except StopIteration:
                                break
                        else:
                            break
                cur += 1
    return download_directory

@app.route('/search', methods=['GET'])
def search_song():
    query = request.args.get('q')
    search_type = request.args.get('type', 'track')

    if not query:
        return jsonify({'error': 'Query parameter is required'}), 400

    RSF = RedseaSessionFile('./config/sessions.pk')
    preset = PRESETS['default']  # Use the default preset or modify as needed
    preset['quality'] = []
    preset['quality'].append('HI_RES') if preset['MQA_FLAC_24'] else None
    preset['quality'].append('LOSSLESS') if preset['FLAC_16'] else None
    preset['quality'].append('HIGH') if preset['AAC_320'] else None
    preset['quality'].append('LOW') if preset['AAC_96'] else None

    md = MediaDownloader(TidalApi(RSF.load_session('TV')), preset, Tagger(preset))


    searchresult = md.search_for_id([search_type, query])

    if search_type == 'track':
        searchtype = 'tracks'
    elif search_type == 'album':
        searchtype = 'albums'
    else:
        return jsonify({'error': 'Invalid search type'}), 400

    numberofsongs = min(searchresult[searchtype]['totalNumberOfItems'], 20)
    results = []

    for i in range(numberofsongs):
        song = searchresult[searchtype]['items'][i]
        if song['audioModes'] != ['DOLBY_ATMOS']:
            continue


        explicittag = " [E]" if song['explicit'] else ""

        result = {
            'index': i + 1,
            'artist': song['artists'][0]['name'],
            'title': song['title'],
            'explicit': explicittag,
            'song': song
        }
        results.append(result)

    return jsonify(results)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
