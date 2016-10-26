# -*-*- encoding: utf-8 -*-*-

import os
import re
import sys
import time
import sys
import urlparse
import json
import datetime
import uuid
import hashlib
import threading
import Queue
import functools
import traceback

from bs4 import BeautifulSoup

from flask.ext.wtf import TextField, PasswordField, Required, URL, ValidationError

from labmanager.forms import AddForm
from labmanager.rlms import register, Laboratory, CacheDisabler, LabNotFoundError
from labmanager.rlms.base import BaseRLMS, BaseFormCreator, Capabilities, Versions

    
def dbg(msg):
    if DEBUG:
        print "[%s]" % time.asctime(), msg
        sys.stdout.flush()

def dbg_lowlevel(msg, scope):
    if DEBUG_LOW_LEVEL:
        print "[%s][%s][%s]" % (time.asctime(), threading.current_thread().name, scope), msg
        sys.stdout.flush()


class PhETAddForm(AddForm):

    DEFAULT_URL = 'http://phet.colorado.edu/en/'
    DEFAULT_LOCATION = 'Colorado, USA'
    DEFAULT_PUBLICLY_AVAILABLE = True
    DEFAULT_PUBLIC_IDENTIFIER = 'phet'
    DEFAULT_AUTOLOAD = True

    def __init__(self, add_or_edit, *args, **kwargs):
        super(PhETAddForm, self).__init__(*args, **kwargs)
        self.add_or_edit = add_or_edit

    @staticmethod
    def process_configuration(old_configuration, new_configuration):
        return new_configuration

class PhETFormCreator(BaseFormCreator):

    def get_add_form(self):
        return PhETAddForm

FORM_CREATOR = PhETFormCreator()

ALL_LINKS = None

MIN_TIME = datetime.timedelta(hours=24)

def get_languages():
    all_links = retrieve_all_links()
    languages = set()
    for link_data in all_links.values():
        languages.update(link_data['localized'].keys())

    return sorted(list(languages))

def retrieve_all_links():
    KEY = 'get_links'
    all_links = PHET.cache.get(KEY, min_time = MIN_TIME)
    if all_links:
        return all_links

    # If it is in a global variable
    all_links = ALL_LINKS
    if all_links:
        return all_links

    all_links = {
        # "http://phet.colorado.edu/en/simulation/acid-base-solutions" : {
        #      # lang_code: {
        #            'link' : 'http://phet.colorado.edu/pt/simulation/acid-base-solutions',
        #            'name' : 'Localized name',
        #            'run_url': '<html to be loaded>'
        #      # }
        # }
    }

    contents = PHET.cached_session.get("https://phet.colorado.edu/services/metadata/1.0/simulations?format=json").json()
    available_names = [ x['name'] for x in contents['projects'] ]
    
    categories = {}
    fetch_children_recursively(contents['categories'], contents['categories']['1'], categories, 10)

    levels = dict([ 
                    (v['name'], v['simulationIds']) 
                    for v in contents['categories'].values() 
                    if v['name'] in ('high-school', 'university', 'elementary-school', 'middle-school') 
            ])
    
    for simulation in contents['projects']:
        current_name = simulation['name']
        if ('html/' + simulation['name']) in available_names:
            continue

        for real_sim in simulation['simulations']:
            link = "http://phet.colorado.edu/en/simulation/%s" % real_sim['name']

            sim_links = {
                'localized': {
                    # lang_code: {
                    #     link
                    #     name
                    #     run_url
                    # }
                },
                'metadata': {
                    'domains': [],
                    'age_ranges': [],
                    'description': real_sim['description']['en'],
                }
            }
            for category_name, simulation_ids in categories.iteritems():
                if simulation['id'] in simulation_ids:
                    sim_links['metadata']['domains'].append(category_name)

            for level_name, simulation_ids in levels.iteritems():
                if simulation['id'] in simulation_ids:
                    if level_name == 'university':
                        sim_links['metadata']['age_ranges'].append('>18')
                    elif level_name == 'high-school':
                        sim_links['metadata']['age_ranges'].append('14-16')
                        sim_links['metadata']['age_ranges'].append('16-18')
                    elif level_name == 'middle-school':
                        sim_links['metadata']['age_ranges'].append('10-12')
                        sim_links['metadata']['age_ranges'].append('12-14')
                    elif level_name == 'elementary-school':
                        sim_links['metadata']['age_ranges'].append('8-10')
                        sim_links['metadata']['age_ranges'].append('6-8')
                        sim_links['metadata']['age_ranges'].append('<6')

            available_langs = [ x['locale'] for x in real_sim['localizedSimulations'] ]
            for localized_sim in real_sim['localizedSimulations']:
                lang = localized_sim['locale']
                if '_' in lang:
                    lang = lang.split('_')[0]
                    if lang in available_langs:
                        # 'es' has higher priority over 'es_PE' 
                        continue

                sim_links['localized'][lang] = {
                    'link' : link,
                    'name': localized_sim['title'],
                    'run_url': localized_sim['runUrl'].replace('https://', 'http://'),
                }

            if sim_links['localized']:
                all_links[link] = sim_links

    PHET.cache[KEY] = all_links
    return all_links

def fetch_children_recursively(phet_categories, node, results, max_depth):
    if max_depth == 0:
        return

    exceptions = ('by-device', 'by-level', 'html', 'new')

    for children_id in node['childrenIds']:
        current_children = phet_categories[unicode(children_id)]
        if current_children['name'] in exceptions:
            continue

        results[current_children['name']] = current_children['simulationIds']
        fetch_children_recursively(phet_categories, current_children, results, max_depth - 1)

def retrieve_labs():
    KEY = 'get_laboratories'
    laboratories = PHET.cache.get(KEY, min_time = MIN_TIME)
    if laboratories:
        return laboratories

    dbg("get_laboratories not in cache")

    links = retrieve_all_links()
    laboratories = []
    for link, link_data in links.iteritems():
        if 'en' in link_data['localized']:
            cur_name = link_data['localized']['en']['name']
            lab = Laboratory(name = cur_name, laboratory_id = link, autoload = True, domains=link_data['metadata']['domains'], age_ranges=link_data['metadata']['age_ranges'], description=link_data['metadata']['description'])
            laboratories.append(lab)

    PHET.cache[KEY] = laboratories
    return laboratories

class RLMS(BaseRLMS):

    def __init__(self, configuration, *args, **kwargs):
        self.configuration = json.loads(configuration or '{}')

    def get_version(self):
        return Versions.VERSION_1

    def get_capabilities(self):
        return [ Capabilities.WIDGET, Capabilities.TRANSLATION_LIST ]
        # return [ Capabilities.WIDGET, Capabilities.TRANSLATIONS ]

    def get_laboratories(self, **kwargs):
        return retrieve_labs()

    def _convert_i18n_strings(self, strings):
        translations = {
            # lang : {
            #      key: {
            #          'namespace': 'foo',
            #          'value': 'bar',
            #      }
            # }
        }
        for lang in strings.keys():
            translations[lang] = {}
            for key, value in strings[lang].items():
                if '/' in key:
                    namespace, _ = key.split('/', 1)
                else:
                    namespace = None
                translations[lang][key] = {
                    'value': value,
                }

                if namespace is not None:
                    translations[lang][key]['namespace'] = namespace

        return translations

    def get_translation_list(self, laboratory_id):
        KEY = 'languages_{}'.format(laboratory_id)
        languages = PHET.cache.get(KEY)
        if languages is None:
            languages = []
            links = retrieve_all_links()
            link_data = links.get(laboratory_id)
            if link_data is not None:
                languages = list(link_data['localized'].keys())
            PHET.cache[KEY] = languages

        return {
            'supported_languages' : languages
        }

    def get_translations(self, laboratory_id):
        translations = PHET.cache.get(laboratory_id)
        if translations:
            return translations
        RESPONSE = {
            'mails' : [
                # TODO: hardcoded
                'pablo.orduna@deusto.es',
            ],
            'translations' : {}
        }

        try:            
            data = self.reserve(laboratory_id, None, None, None, None, None, None)
            url = data['load_url']
            
            name = url.split('/')[-3]
            string_map_url = url.rsplit('/', 1)[0] + '/' + name + '_string-map.json'
            print string_map_url
            r = PHET.cached_session.get(string_map_url)
            if r.status_code == 200:
                try:
                    converted_strings = self._convert_i18n_strings(r.json())
                except:
                    traceback.print_exc()
                else:
                    print converted_strings
                    RESPONSE['translations'].update(converted_strings)
                    return RESPONSE

            r = PHET.cached_session.get(url)
            if r.status_code != 200:
                return RESPONSE

            i18n_line = None
            for line in r.text.splitlines():
                if line.strip().startswith('window.phet.chipper.strings'):
                    i18n_line = line
                    break

            if i18n_line is None:
                return RESPONSE

            json_contents = i18n_line.split('=', 1)[1].strip()
            json_contents = json_contents.rsplit(';', 1)[0]
            try:
                contents = json.loads(json_contents)
            except:
                return RESPONSE

            return RESPONSE
        except:
            traceback.print_exc()
            return RESPONSE
        

    def reserve(self, laboratory_id, username, institution, general_configuration_str, particular_configurations, request_payload, user_properties, *args, **kwargs):
        locale = kwargs.get('locale', 'en')
        if '_' in locale:
            locale = locale.split('_')[0]
        KEY = '_'.join((laboratory_id, locale))
        response = PHET.cache.get(KEY, min_time = MIN_TIME)
        if response is not None:
            return response

        dbg_current = functools.partial(dbg_lowlevel, scope = '%s::%s' % (laboratory_id, locale))
        dbg_current("Retrieving links")
        links = retrieve_all_links()
        dbg_current("Links retrieved")
        link_data = links.get(laboratory_id)

        if link_data is None:
            raise LabNotFoundError("Lab %s not found" % laboratory_id)

        localized = link_data['localized'].get(locale)
        if localized is None:
            NEW_KEY = '_'.join((laboratory_id, 'en'))
            response = PHET.cache.get(NEW_KEY, min_time = MIN_TIME)
            if response:
                PHET.cache[KEY] = response
                return response

            localized = link_data['localized']['en']
        
        url = localized['run_url']

        response = {
            'reservation_id' : url,
            'load_url' : url
        }
        dbg_current("Storing in cache")
        PHET.cache[KEY] = response
        dbg_current("Finished")
        return response

    def load_widget(self, reservation_id, widget_name, **kwargs):
        return {
            'url' : reservation_id
        }

    def list_widgets(self, laboratory_id, **kwargs):
        default_widget = dict( name = 'default', description = 'Default widget' )
        return [ default_widget ]

class _QueueTaskProcessor(threading.Thread):
    def __init__(self, number, queue):
        threading.Thread.__init__(self)
        self.number = number
        self.setName("QueueProcessor-%s" % number)
        self.queue = queue
        self._current = None

    def run(self):
        cache_disabler = CacheDisabler()
        cache_disabler.disable()
        try:
            while True:
                try:
                    t = self.queue.get_nowait()
                except Queue.Empty:
                    break
                else:
                    self._current = t
                    try:
                        t.run()
                    except:
                        print("Error in task: %s" % t)
                        traceback.print_exc()
        finally:
            cache_disabler.reenable()
        self._current = None

        dbg("%s: finished" % self.name)

    def __repr__(self):
        return "_QueueTaskProcessor(number=%r, current=%r; alive=%r)" % (self.number, self._current, self.isAlive())

NUM_THREADS = 32
if os.environ.get('G4L_PHET_THREADS'):
    NUM_THREADS = int(os.environ['G4L_PHET_THREADS'])

def _run_tasks(tasks, threads = NUM_THREADS):
    queue = Queue.Queue()
    for task in tasks:
        queue.put(task)
    
    task_processors = []
    for task_processor_number in range(threads):
        task_processor = _QueueTaskProcessor(task_processor_number, queue)
        task_processor.start()
        task_processors.append(task_processor)

    any_alive = True
    count = 0
    while any_alive:
        alive_threads = []
        for task_processor in task_processors:
            if task_processor.isAlive():
                alive_threads.append(task_processor)

        any_alive = len(alive_threads) > 0

        if any_alive:
            count = count + 1
            if count % 60 == 0:
                if len(alive_threads) > 5:
                    dbg("%s live processors" % len(alive_threads))
                    print("[%s] %s live processors" % (time.asctime(), len(alive_threads)))
                else:
                    dbg("%s live processors: %s" % (len(alive_threads), ', '.join([ repr(t) for t in alive_threads ])))
                    print("[%s] %s live processors: %s" % (time.asctime(), len(alive_threads), ', '.join([ repr(t) for t in alive_threads ])))
                sys.stdout.flush()

        try:
            time.sleep(1)
        except:
            # If there is an exception (such as keyboardinterrupt, or kill process..)
            for task in tasks:
                task.stop()

            # Delete everything in the queue (so the task stops) and re-raise the exception
            while True:
                try:
                    queue.get_nowait()
                except Queue.Empty:
                    break
            raise

    dbg("All processes are over")


class _QueueTask(object):
    def __init__(self, laboratory_id, language):
        self.laboratory_id = laboratory_id
        self.language = language
        self.stopping = False

    def __repr__(self):
        return '_QueueTask(laboratory_id=%r, language=%r, stopping=%r)' % (self.laboratory_id, self.language, self.stopping)

    def stop(self):
        self.stopping = True

    def run(self):
        if self.stopping:
            return

        rlms = RLMS("{}")
        dbg(' - %s: %s lang: %s' % (threading.current_thread().name, self.laboratory_id, self.language))
        rlms.reserve(self.laboratory_id, 'tester', 'foo', '', '', '', '', locale = self.language)

def populate_cache():
    rlms = RLMS("{}")
    dbg("Retrieving labs")
    LANGUAGES = get_languages()
    global ALL_LINKS
    ALL_LINKS = retrieve_all_links()

    try:
        tasks = []
        for lab in rlms.get_laboratories():
            for lang in LANGUAGES:
                tasks.append(_QueueTask(lab.laboratory_id, lang))

        _run_tasks(tasks)

        dbg("Finished")
    finally:
        ALL_LINKS = None
        sys.stdout.flush()
        sys.stderr.flush()

PHET = register("PhET", ['1.0'], __name__)
PHET.add_global_periodic_task('Populating cache', populate_cache, hours = 23)

DEBUG = PHET.is_debug() or (os.environ.get('G4L_DEBUG') or '').lower() == 'true' or False
DEBUG_LOW_LEVEL = DEBUG and (os.environ.get('G4L_DEBUG_LOW') or '').lower() == 'true'

if DEBUG:
    print("Debug activated")

if DEBUG_LOW_LEVEL:
    print("Debug low level activated")

sys.stdout.flush()

def main():
    with CacheDisabler():
        rlms = RLMS("{}")
        t0 = time.time()
        laboratories = rlms.get_laboratories()
        tf = time.time()
        print len(laboratories), (tf - t0), "seconds"
        print
        print laboratories[:10]
        print
        # print get_languages()
        # foo = {}
        # populate_links('ar', foo)
        # print sorted(foo.keys())
        # print retrieve_all_links()['http://phet.colorado.edu/en/simulation/density']
        print rlms.reserve('http://phet.colorado.edu/en/simulation/beers-law-lab', 'tester', 'foo', '', '', '', '', locale = 'es_ALL')
        print rlms.reserve('http://phet.colorado.edu/en/simulation/beers-law-lab', 'tester', 'foo', '', '', '', '', locale = 'xx_ALL')
        print rlms.reserve('http://phet.colorado.edu/en/simulation/acid-base-solutions', 'tester', 'foo', '', '', '', '', locale = 'es_ALL')
        print rlms.reserve('http://phet.colorado.edu/en/simulation/acid-base-solutions', 'tester', 'foo', '', '', '', '', locale = 'xx_ALL')
        print rlms.get_translation_list('http://phet.colorado.edu/en/simulation/acid-base-solutions')
    
        try:
            rlms.reserve('identifier-not-found', 'tester', 'foo', '', '', '', '', locale = 'xx_ALL')
        except LabNotFoundError:
            print "Captured error successfully"

    return
    for lab in laboratories[:5]:
        for lang in ('en', 'pt'):
            t0 = time.time()
            print rlms.reserve(lab.laboratory_id, 'tester', 'foo', '', '', '', '', locale = lang)
            tf = time.time()
            print tf - t0, "seconds"
    

if __name__ == '__main__':
    main()
