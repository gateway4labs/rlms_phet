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
from labmanager.rlms import register, Laboratory, CacheDisabler
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

def phet_url(url):
    return "http://phet.colorado.edu%s" % url

MIN_TIME = datetime.timedelta(hours=24)

def get_languages():
    KEY = 'get_languages'
    languages = PHET.cache.get(KEY, min_time = MIN_TIME)
    if languages:
        return languages

    listing_url = phet_url("/en/simulations/index")
    index_html = PHET.cached_session.timeout_get(listing_url).text
    soup = BeautifulSoup(index_html, "lxml")
    languages = set([])
    for translation_link_option in soup.find_all("option"):
        option_value = translation_link_option.get('value') or ''
        if '/simulations/index' in option_value:
            language = option_value.split('https://phet.colorado.edu/')[1].split('/')[0]
            languages.add(language)
    languages = list(languages)
    languages.sort()
    PHET.cache[KEY] = languages
    return languages

def populate_links(lang, all_links):
    listing_url = phet_url("/%s/simulations/index" % lang)

    index_html = PHET.cached_session.timeout_get(listing_url).text
    soup = BeautifulSoup(index_html, 'lxml')

    laboratories = []

    lang = lang.split('_')[0]
    for h2 in soup.find_all("h2"):
        parent_identifier = h2.parent.get('id')
        if parent_identifier and len(parent_identifier) == 1:
            for link in h2.parent.find_all("a"):
                link_href = phet_url(link.get('href'))
                name = link.find("span").text

                # http://phet.colorado.edu/es/simulation/acid-base-solutions => simulation/acid-base-solutions
                relative_path = urlparse.urlparse(link_href).path.split('/',2)[-1]
                if relative_path not in all_links:
                    all_links[relative_path] = {}

                all_links[relative_path][lang] = {
                    'link' : link_href,
                    'name' : name,
                }


def retrieve_all_links():
    KEY = 'get_links'
    all_links = PHET.cache.get(KEY, min_time = MIN_TIME)
    if all_links:
        return all_links

    # If it is in a global variable
    all_links = ALL_LINKS
    if all_links:
        return all_links

    all_links = {}
    previous_short_langs = set()
    for lang in get_languages():
        short_lang = lang.split('_')[0]
        if short_lang in previous_short_langs:
            continue
        previous_short_langs.add(short_lang)
        populate_links(lang, all_links)

    new_links = {}
    # Convert relative links into absolute links
    for link, link_data in all_links.iteritems():
        new_links[link_data['en']['link']] = link_data

    PHET.cache[KEY] = new_links
    return new_links

def retrieve_labs():
    KEY = 'get_laboratories'
    laboratories = PHET.cache.get(KEY, min_time = MIN_TIME)
    if laboratories:
        return laboratories

    dbg("get_laboratories not in cache")

    links = retrieve_all_links()
    laboratories = []
    for link, link_data in links.iteritems():
        if 'en' in link_data:
            cur_name = link_data['en']['name']
            lab = Laboratory(name = cur_name, laboratory_id = link, autoload = True)
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
        languages = PHET.rlms_cache.get(KEY)
        if languages is None:
            languages = []
            links = retrieve_all_links()
            link_data = links.get(laboratory_id)
            if link_data is not None:
                languages = list(link_data.keys())
            PHET.rlms_cache[KEY] = languages

        return {
            'supported_languages' : languages
        }

    def get_translations(self, laboratory_id):
        translations = PHET.rlms_cache.get(laboratory_id)
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
            link = laboratory_id
        else:
            if locale in link_data:
                link = link_data[locale]['link']
            else:
                # If the language is not in the list of labs, 
                # use the English version
                NEW_KEY = '_'.join((laboratory_id, 'en'))
                response = PHET.cache.get(NEW_KEY, min_time = MIN_TIME)
                if response:
                    PHET.cache[KEY] = response
                    return response

                link = link_data['en']['link']
        
        dbg_current("Retrieving link: %s" % link)
        laboratory_html = PHET.cached_session.timeout_get(link).text
        dbg_current("Link retrieved")
        soup = BeautifulSoup(laboratory_html, 'lxml')

        url  = ""

        # If there's a "Run in HTML5" button
        html5_url = soup.find("a", class_="sim-button", text=re.compile("HTML5"))

        if html5_url:
            # Then that's the URL
            url = html5_url.get("href")
        else:
            # Otherwise, if there is a embeddable-text
            embeddable_text = soup.find(id="embeddable-text")
            if embeddable_text is None:
                print("Error: %s doesn't have an 'embeddable-text'. Expect a None error" % link)
                sys.stdout.flush()

            embed_text = embeddable_text.text
    
            # Then, check what's inside:
            embed_soup = BeautifulSoup(embed_text, 'lxml')

            # If it's an iframe, the src is the URL
            iframe_tag = embed_soup.find("iframe")
            if iframe_tag:
                url = iframe_tag.get("src")
            else:
                # Otherwise, the link is the URL
                a_tag = embed_soup.find("a")
                url = a_tag.get("href")

        if url and not url.startswith(("http://", "https://")):
            url = phet_url(url)

        if url.startswith('http://phet.colorado.eduhttps://'):
            url = url[len('http://phet.colorado.edu'):]

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
    print rlms.reserve('http://phet.colorado.edu/en/simulation/density', 'tester', 'foo', '', '', '', '', locale = 'el_ALL')
    print rlms.reserve('http://phet.colorado.edu/en/simulation/density', 'tester', 'foo', '', '', '', '', locale = 'pt_ALL')
    print rlms.reserve('http://phet.colorado.edu/en/simulation/density', 'tester', 'foo', '', '', '', '', locale = 'ar_ALL')
    print rlms.reserve('http://phet.colorado.edu/en/simulation/density', 'tester', 'foo', '', '', '', '', locale = 'es_ALL')
    for lab in laboratories[:5]:
        for lang in ('en', 'pt'):
            t0 = time.time()
            print rlms.reserve(lab.laboratory_id, 'tester', 'foo', '', '', '', '', locale = lang)
            tf = time.time()
            print tf - t0, "seconds"
    

if __name__ == '__main__':
    main()
