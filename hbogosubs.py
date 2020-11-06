#!/usr/bin/env python3

import argparse
import http.client
import io
import json
import logging
import os
import re
import signal
import sys
import time
import uuid
from getpass import getpass
from operator import truediv
from urllib.parse import urlparse

import bs4
import pathvalidate
import pycaption
import pycountry
import requests
import xmltodict
from pymp4.parser import Box
from tqdm import tqdm


class HBOGoSubtitleDownloader(object):
    def __init__(self):
        self.logger = logging.getLogger('hbogosubs')

        self.session = requests.Session()
        self.session.hooks = {
            'response': self.check_error,
        }
        self.session.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                          '(KHTML, like Gecko) Chrome/70.0.3538.102 Safari/537.36',
        }

        self.operators = {}

        self.operator_id = None
        self.username = None
        self.password = None

        self.device_registered = False
        self.device_id = None
        self.device_indiv = None
        self.customer_id = None
        self.session_id = None
        self.token = None

    def check_error(self, r, *args, log=True, fatal=True, **kwargs):
        ok = True
        errors = []

        if r.headers.get('content-type') == 'application/json':
            resp = r.json()

            if resp.get('Error'):
                ok = False
                errors.append(resp['Error']['Message'])

            if resp.get('ErrorMessage'):
                ok = False
                errors.append(resp['ErrorMessage'])

            if resp.get('technicalErrorMessage'):
                ok = False
                data = resp['technicalErrorMessage'].split('\n')[-1]
                try:
                    data = json.loads(data)
                except json.decoder.JSONDecodeError:
                    errors.append(data)
                else:
                    errors.append(data['technicalErrorMessage'])

        if not r.ok:
            ok = False
            message = f'HTTP Error {r.status_code}: {http.client.responses[r.status_code]}'

            if errors:
                self.logger.debug(message)
            else:
                self.logger.error(message)

        if log:
            for error in errors:
                self.logger.error(error)

        r.check_ok = ok

        if fatal and not ok:
            sys.exit(1)

    def configure(self):
        os.makedirs(self.config_dir, exist_ok=True)
        os.makedirs(self.args.output_dir, exist_ok=True)

        self.get_operators()

        try:
            with open(self.config_file, 'r') as fd:
                self.logger.info('Found existing config')

                config = json.load(fd)

                self.operator_id = config['operator_id']
                self.username = config['username']
                self.password = config['password']

                if self.operator_id not in self.operators:
                    self.logger.warning(f'Unknown operator ID: {self.operator_id}')
        except (FileNotFoundError, KeyError) as e:
            if isinstance(e, KeyError):
                self.logger.error('Config file is invalid, resetting')
            else:
                self.logger.info('Starting initial config')

            print()

            self.operator_id = self.select_operator()

            if self.operators[self.operator_id]['type']:
                self.username = input('Enter email: ')
            else:
                self.username = input('Enter username: ')

            self.password = getpass('Enter password (will not be displayed): ')

            print()

            with open(self.config_file, 'w') as fd:
                json.dump({
                    'operator_id': self.operator_id,
                    'username': self.username,
                    'password': self.password,
                }, fd, indent=2)

    def get_operators(self):
        self.logger.info('Getting operator list...')

        operators = []

        for url in (f'https://api.ugw.hbogo.eu/v3.0/Operators/{self.region.alpha_3}/JSON/ENG/COMP',
                    f'https://{self.region.alpha_2}gwapi.hbogo.eu/v2.1/Operators/json/ENG/COMP'):
            r = self.session.get(url, hooks={
                'response': lambda r, *args, **kwargs: self.check_error(r, fatal=False),
            })
            resp = r.json()
            if r.check_ok:
                operators += resp.get('Items', [])

        self.logger.debug('Available operators:')

        for operator in operators:
            self.logger.debug(f'{operator["Id"]} - {operator["Name"]} - {operator.get("Type")}')

            op_id = operator['Id']
            op_name = operator['Name']
            op_type = operator.get('Type')

            self.operators[op_id] = {
                'name': op_name,
                'type': op_type,
            }

    def select_operator(self):
        if not self.operators:
            self.logger.error('No operators available')
            sys.exit(1)

        operator_ids = list(self.operators.keys())

        print('Available operators:')
        for (i, (op_id, operator)) in enumerate(self.operators.items()):
            s = f'[{i}] {operator["name"]}'

            if operator['type'] == 'D2_C':
                s += ' (direct login)'

            if self.logger.getEffectiveLevel() == logging.DEBUG:
                s += f' [{op_id}]'

            print(s)
        print()

        operator_num = None
        while operator_num is None:
            try:
                operator_num = int(input('Enter operator number: '))

                if not (0 <= operator_num < len(operator_ids)):
                    raise ValueError('invalid operator number')

                return operator_ids[operator_num]
            except ValueError:
                print('\nInvalid input, please try again.')
                operator_num = None
        print()

    def silentregister(self):
        self.logger.debug('Generating device ID...')

        self.device_registered = True
        self.device_id = str(uuid.uuid4())

        with open(self.deviceinfo_file, 'w') as fd:
            json.dump({
                'device_id': self.device_id,
            }, fd, indent=None, separators=(',', ':'))

    def login(self):
        if not self.device_id:
            try:
                with open(self.deviceinfo_file, 'r') as fd:
                    self.logger.info('Found existing device info')
                    deviceinfo = json.load(fd)
                    self.device_id = deviceinfo['device_id']
                    self.logger.debug(f'Device ID: {self.device_id}')
            except FileNotFoundError:
                self.silentregister()

        self.logger.info('Logging in...')

        payload = {
            'Action': 'L',
            'CurrentDevice': {
                'Individualization': self.device_id,
                'Platform': 'COMP',
            },
            'EmailAddress': self.username,
            'IsAnonymus': True,
            'Nick': self.username,
            'OperatorId': self.operator_id,
            'Password': self.password,
        }

        if self.operators[self.operator_id]['type']:
            auth_url = f'https://api.ugw.hbogo.eu/v3.0/Authentication/{self.region.alpha_3}/JSON/ENG/COMP'
        else:
            auth_url = f'https://{self.region.alpha_2}gwapi.hbogo.eu/v2.1/Authentication/json/ENG/COMP'

        kwargs = {'json': payload}

        if self.device_registered:
            kwargs.update({
                'hooks': {
                    'response': lambda r, *args, **kwargs: self.check_error(r, log=False, fatal=False),
                }
            })

        r = self.session.post(auth_url, **kwargs)
        resp = r.json()
        self.logger.debug(resp)

        if not r.check_ok:
            self.logger.warning('Login failed, attempting to re-register device in 5 seconds')
            time.sleep(5)
            self.silentregister()
            return self.login()

        self.device_id = resp['Customer']['CurrentDevice']['Id']
        self.device_indiv = resp['Customer']['CurrentDevice']['Individualization']
        self.customer_id = resp['Customer']['Id']

        self.logger.debug(f'Got device ID: {self.device_id}')
        self.logger.debug(f'Got device indiv: {self.device_indiv}')
        self.logger.debug(f'Got customer ID: {self.customer_id}')

        self.session_id = resp['SessionId']
        self.token = resp['Token']

        self.session.headers['GO-CustomerID'] = self.customer_id
        self.session.headers['GO-SessionId'] = self.session_id
        self.session.headers['GO-TOKEN'] = self.token

        self.logger.info('Login successful')
        self.logger.debug(resp)

    def download_url(self, url):
        self.logger.info(f'Downloading: {url!r}')

        r = self.session.get(url)

        soup = bs4.BeautifulSoup(r.text, 'lxml-html')

        try:
            canonical_url = soup.find('link', rel='canonical').get('href')
        except AttributeError:
            self.logger.error(' '.join(soup.find('main').stripped_strings))
            sys.exit(1)
        canonical_path = urlparse(canonical_url).path

        details = soup.find(class_='modal-details')
        raw_content_type = details.get('data-type')

        if raw_content_type == 'season':
            sids = []
            wanted_ep = None

            selected_season = soup.select_one('.season-tab a.selected')
            selected_ep = soup.find(class_='episode-detail')

            content_type = None

            if selected_season:
                if urlparse(selected_season.get('href')).path == canonical_path:
                    content_type = 'season'
                elif selected_ep:
                    content_type = 'episode'
                    wanted_ep = int(selected_ep.get('data-episode-number'))

                if content_type:
                    sids.append(int(selected_season.get('data-season-id')))

            if not sids:
                content_type = 'show'
                sids += [int(x.get('data-season-id')) for x in soup.select('.season-tab a')]

            self.logger.info(f'Content type: {content_type.title()}')
            self.download_show(sids, wanted_ep)
        elif raw_content_type == 'movie':
            self.logger.info('Content type: Movie')

            external_id = int(details.get('data-external-id'))
            self.download_movie(external_id)

    def download_show(self, sids, wanted_ep):
        self.logger.debug(f'Season IDs: {sids}')
        self.logger.debug(f'Wanted episode: {wanted_ep}')

        for sid in sids:
            self.logger.debug(f'Processing season ID: {sid}')

            r = self.session.get(f'https://hbogo.{self.region.alpha_2}/api/modal/meta/season/{sid}/ext')
            resp = r.json()

            series = resp['english_title']

            for ep in resp['episodes']:
                season = int(ep['season_number'])
                episode = int(ep['episode_number'])

                if wanted_ep and episode != wanted_ep:
                    self.logger.debug(f'Skipping episode {episode} '
                                      f'because we want episode {wanted_ep}')
                    continue

                content_id = ep['media_id']
                content_name = f'{series} S{season:02}E{episode:02}'

                print()
                self.logger.info(f'Downloading subtitles for {content_name}')
                self.logger.debug(f'Content ID: {content_id}')

                self.download_content(content_id, content_name)

                if wanted_ep:
                    self.logger.debug(f'Skipping further episodes '
                                      f'because we want episode {wanted_ep}')
                    break

    def download_movie(self, external_id):
        self.logger.debug(f'External ID: {external_id}')

        r = self.session.get(f'https://{self.region.alpha_2}api.hbogo.eu/v8/ContentByExternalId/json/ENG/COMP/{external_id}/1')
        resp = r.json()

        content_id = resp['Id']
        content_name = f'{resp["Name"]} {resp["ProductionYear"]}'

        self.download_content(content_id, content_name)

    def download_content(self, content_id, content_name):
        payload = {
            'Purchase': {
                '@xmlns': 'go:v8:interop',
                '@xmlns:i': 'http://www.w3.org/2001/XMLSchema-instance',
                'AllowHighResolution': 'false',
                'ContentId': content_id,
                'CustomerId': self.customer_id,
                'Individualization': self.device_indiv,
                'OperatorId': self.operator_id,
                'IsFree': 'false',
                'RequiredPlatform': 'COMP',
                'UseInteractivity': 'false',
            },
        }

        raw_payload = xmltodict.unparse(payload, full_document=False)

        r = self.session.post(
            f'https://{self.region.alpha_2}api.hbogo.eu/v8/Purchase/json/ENG/COMP',
            data=raw_payload,
        )
        resp = r.json()

        self.logger.debug(json.dumps(resp, indent=2))

        if resp.get('Error'):
            self.logger.error(resp['Error']['Message'])
            self.logger.debug(resp)
            sys.exit(1)

        subtitles = resp['Purchase'].get('Subtitles')
        sub_tracks = []
        if subtitles and not self.args.force_ism:
            for sub in subtitles:
                if not sub['Url']:
                    continue

                sub_tracks.append({
                    'url': sub['Url'],
                    'format': sub['Url'].split('.')[-1],
                    'language': sub['Code'].lower(),
                })

        if sub_tracks:
            self.logger.info('Found direct subtitle links')
            self.download_subtitles(sub_tracks, content_name)
        else:
            self.logger.info('Downloading subtitles from manifest')
            self.download_from_ism(resp['Purchase']['MediaUrl'], content_name, self.args.output_format)

    def download_subtitles(self, sub_tracks, output_name):
        if self.args.output_format != 'srt' and not self.args.force_ism:
            self.logger.warning(
                f'Requested output format {self.args.output_format!r} '
                f'will be ignored for direct subtitle download. '
                f'You may want to use the force_ism (-F) option.',
            )

        for (index, track) in enumerate(sub_tracks):
            index += 1

            fmt = track['format']
            if fmt != 'srt':
                self.logger.error('Unsupported subtitle format: {fmt!r}')
                sys.exit(1)

            lang = track['language']

            output = f'{output_name.replace(" ", ".")}.{lang}.{index}.srt'
            output = pathvalidate.sanitize_filename(output)
            output = os.path.join(self.args.output_dir, output)
            self.logger.info(f'Saving subtitle track #{index} to {output}')

            r = self.session.get(track['url'], hooks={
                'response': lambda r, *args, **kwargs: self.check_error(r, fatal=False),
            })

            if r.content.startswith(b'\xef\xbb\xbf'):
                self.logger.debug('Encoding detected as: utf-8-sig')
                r.encoding = 'utf-8-sig'
            else:
                self.logger.debug(f'Encoding detected as: {r.encoding}')

            os.makedirs(self.args.output_dir, exist_ok=True)

            with open(output, 'wb') as fd:
                fd.write(r.text.encode('utf-8-sig'))

    @staticmethod
    def ismt_to_ttml(ismt_data):
        fd = io.BytesIO(ismt_data)

        while True:
            x = Box.parse_stream(fd)
            if x.type == b'mdat':
                return x.data

    def download_from_ism(self, url, output_name, output_format):
        r = self.session.get(f'{url}/manifest')
        manifest = xmltodict.parse(r.content, force_list={'StreamIndex', 'c'})
        self.logger.debug(json.dumps(manifest, indent=4))

        streams = [x for x in manifest['SmoothStreamingMedia']['StreamIndex'] if x['@Type'] == 'text']

        for (index, stream) in enumerate(streams):
            lang = stream['@Language'].lower()

            fmt = stream['QualityLevel']['@FourCC'].upper()
            if fmt != 'TTML':
                self.logger.error(f'Stream has unsupported subtitle format: {fmt!r}')
                sys.exit(1)

            index += 1
            output = f'{output_name.replace(" ", ".")}.{lang}.{index}.{output_format}'
            output = pathvalidate.sanitize_filename(output)
            output = os.path.join(self.args.output_dir, output)
            self.logger.info(f'Downloading subtitle track #{index} ({lang})')

            path = stream['@Url'].replace('{bitrate}', stream['QualityLevel']['@Bitrate'])
            t = 0
            ts = []

            for c in stream['c']:
                if c.get('@t'):
                    t = int(c['@t'])
                    ts.append(t)

                if not c.get('@d'):
                    # Stream only has a single segment
                    break

                for i in range(c.get('@r', 1)):
                    t += int(c['@d'])
                    ts.append(t)

            ts = ts[:-1]  # Remove nonexistent last segment

            xml = {'tt': {'body': {'div': {'p': []}}}}

            for t in tqdm(ts, unit='seg', disable=self.args.debug):
                seg_url = f'{url}/{path.replace("{start time}", str(t))}'
                seg = self.session.get(seg_url).content

                if not seg:
                    # Empty segment
                    continue

                data = self.ismt_to_ttml(seg).decode('utf-8')

                assert '{{BR}}' not in data, 'input data contains br placeholder'
                data = re.sub(r'<br ?/>', '{{BR}}', data)

                xml_seg = xmltodict.parse(
                    data,
                    force_list={'p'},
                    process_namespaces=True,
                    namespaces={
                        'http://www.w3.org/XML/1998/namespace': None,
                        'http://www.w3.org/2006/10/ttaf1': None,
                        'http://www.w3.org/2006/10/ttaf1#metadata': None,
                        'http://www.w3.org/2006/10/ttaf1#styling': None,
                        'http://www.w3.org/ns/ttml': None,
                    },
                )

                if i == 0:
                    fps_base = xml_seg['tt'].get('@ttp:frameRate')
                    fps_mult = xml_seg['tt'].get('@ttp:frameRateMultiplier')

                    if xml_seg['tt']['body']['div'] is None:
                        xml_seg['tt']['body']['div'] = {'p': []}

                    if fps_base:
                        if fps_mult:
                            mult = [int(x) for x in fps_mult.split(' ')]
                            mult = truediv(*mult)
                        else:
                            mult = 1

                        fps = fps_base * fps_mult
                    else:
                        fps = 30  # Per TTML spec

                div = xml_seg['tt']['body']['div']

                if div is None:
                    # Empty subtitle file
                    continue

                subs = div.get('p', [])

                scale = int(stream['@TimeScale'])
                offset = t / scale

                for p in subs:
                    for a in ('@begin', '@end'):
                        tc = p[a]
                        if '.' in tc:
                            (h, m, s) = [float(x) for x in tc.split(':')]
                            f = 0
                        else:
                            (h, m, s, f) = [int(x) for x in tc.split(':')]
                        total = round(h*3600 + m*60 + s + f/fps + offset, 3)
                        p[a] = f'{total}s'

                    begin = float(p['@begin'][:-1])
                    end = float(p['@end'][:-1])

                    if end < begin:
                        self.logger.error(
                            f'End time is earlier than start time ({end} < {begin})',
                        )
                        return

                xml['tt']['body']['div']['p'].extend(subs)

            xml_data = xmltodict.unparse(xml, pretty=True)
            xml_data = xml_data.replace('{{BR}}', '<br />')

            os.makedirs(self.args.output_dir, exist_ok=True)

            self.logger.info(f'Converting and saving to {output}')

            with open(output, 'wb') as fd:
                if output_format == 'ttml':
                    fd.write(xml_data.encode('utf-8-sig'))
                elif output_format == 'srt':
                    r = pycaption.DFXPReader().read(xml_data)
                    w = pycaption.SRTWriter().write(r)
                    fd.write(w.encode('utf-8-sig'))

    def main(self, args):
        self.args = args

        if getattr(sys, 'frozen', False):
            SCRIPT_PATH = os.path.dirname(sys.executable)
        else:
            SCRIPT_PATH = os.path.dirname(__file__)

        self.logger.debug(f'Script path: {SCRIPT_PATH}')

        if not args.config_dir:
            args.config_dir = os.path.join(SCRIPT_PATH, 'config')

        if args.urls:
            urls = args.urls
        else:
            urls = input('Enter URLs (separated by space): ').split()

        if not urls:
            self.logger.info('No URLs to download')
            sys.exit(0)

        URL_PATTERN = re.compile(r'https?://(?:www\.)?hbogo\.(hu|cz|sk|ro|pl|hr|rs|si|mk|me|bg|ba)/')
        region = None
        for url in urls:
            m = re.match(URL_PATTERN, url)

            if not m:
                self.logger.error(f'Unsupported URL: {url!r}. This script currently only supports HBO GO Europe.')
                sys.exit(1)

            reg = m.group(1)
            if region and reg != region:
                self.logger.error('You may not mix URLs from multiple HBO GO regions in a single invocation.')
                sys.exit(1)
            region = reg
        self.region = pycountry.countries.get(alpha_2=region.upper())
        self.logger.info(f'Region detected as: {self.region.name}')

        self.config_dir = os.path.join(args.config_dir, self.region.alpha_2)
        self.config_file = os.path.join(self.config_dir, 'config.json')
        self.deviceinfo_file = os.path.join(self.config_dir, 'deviceinfo.json')

        downloader.configure()
        downloader.login()

        for url in urls:
            downloader.download_url(url)

        print()
        self.logger.info('Downloads finished')


if __name__ == '__main__':
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    parser = argparse.ArgumentParser(
        prog='hbogosubs',
        formatter_class=lambda prog: argparse.HelpFormatter(prog, max_help_position=30),
    )
    parser.add_argument(
        'urls',
        nargs='*',
        metavar='url',
        help='one or more URLs to a movie, series, season or episode',
    )
    parser.add_argument(
        '-c',
        '--config-dir',
        metavar='DIR',
        help='directory to store configuration in',
    )
    parser.add_argument(
        '-o',
        '--output-dir',
        metavar='DIR',
        default='.',
        help='directory to save downloaded subtitles to',
    )
    parser.add_argument(
        '-F',
        '--force-ism',
        action='store_true',
        help='force downloading subtitles from manifest even if direct URL is available',
    )
    parser.add_argument(
        '-f',
        '--output-format',
        choices=('srt', 'ttml'),
        default='srt',
        help='subtitle output format',
    )
    parser.add_argument(
        '-d',
        '--debug',
        action='store_true',
        help='enable debug logging',
    )
    parser.add_argument(
        '-v',
        '--version',
        action='version',
        version='%(prog)s 1.1.9',
    )
    args = parser.parse_args()

    logging.basicConfig(
        format='[%(asctime)s] %(levelname)s - %(name)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        level=(logging.DEBUG if args.debug else logging.INFO),
    )

    downloader = HBOGoSubtitleDownloader()
    downloader.main(args)
