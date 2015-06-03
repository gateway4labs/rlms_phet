# -*-*- encoding: utf-8 -*-*-

import sys
import time
import re
import sys
import urlparse
import json
import datetime
import uuid
import hashlib
import threading
import Queue

from bs4 import BeautifulSoup

from flask.ext.wtf import TextField, PasswordField, Required, URL, ValidationError

from labmanager.forms import AddForm
from labmanager.rlms import register, Laboratory, CacheDisabler
from labmanager.rlms.base import BaseRLMS, BaseFormCreator, Capabilities, Versions

DEBUG = True
    
def dbg(msg):
    if DEBUG:
        print msg
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
    index_html = PHET.cached_session.get(listing_url).text
    soup = BeautifulSoup(index_html)
    languages = set([])
    for translation_link in soup.find_all("a", class_="translation-link"):
        language = translation_link.get('href').split('/')[1]
        languages.add(language.split('_')[0])
    languages = list(languages)
    languages.sort()
    PHET.cache[KEY] = languages
    return languages

def populate_links(lang, all_links):
    listing_url = phet_url("/%s/simulations/index" % lang)

    index_html = PHET.cached_session.get(listing_url).text
    soup = BeautifulSoup(index_html)

    laboratories = []
    
    for h2 in soup.find_all("h2"):
        parent_identifier = h2.parent.get('id')
        if parent_identifier and len(parent_identifier) == 1 and parent_identifier in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
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
    for lang in get_languages():
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
        return [ Capabilities.WIDGET ]

    def get_laboratories(self, **kwargs):
        return retrieve_labs()

    def reserve(self, laboratory_id, username, institution, general_configuration_str, particular_configurations, request_payload, user_properties, *args, **kwargs):
        locale = kwargs.get('locale', 'en')
        if '_' in locale:
            locale = locale.split('_')[0]
        KEY = '_'.join((laboratory_id, locale))
        response = PHET.cache.get(KEY, min_time = MIN_TIME)
        if response is not None:
            return response
        
        links = retrieve_all_links()
        link_data = links[laboratory_id]
        if locale in link_data:
            link = link_data[locale]['link']
        else:
            link = link_data['en']['link']

        laboratory_html = PHET.cached_session.get(link).text
        soup = BeautifulSoup(laboratory_html)

        url  = ""

        # If there's a "Run in HTML5" button
        html5_url = soup.find("a", class_="sim-button", text=re.compile("HTML5"))

        if html5_url:
            # Then that's the URL
            url = html5_url.get("href")
        else:
            # Otherwise, if there is a embeddable-text
            embed_text = soup.find(id="embeddable-text").text
    
            # Then, check what's inside:
            embed_soup = BeautifulSoup(embed_text)

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

        response = {
            'reservation_id' : url,
            'load_url' : url
        }
        PHET.cache[KEY] = response
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
        self.setName("QueueProcessor-%s" % number)
        self.queue = queue

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
                    t.run()
        finally:
            cache_disabler.reenable()

def _run_tasks(tasks, threads = 32):
    queue = Queue.Queue()
    for task in tasks:
        queue.put(task)
    
    task_processors = []
    for task_processor_number in range(threads):
        task_processor = _QueueTaskProcessor(task_processor_number, queue)
        task_processor.start()
        task_processors.append(task_processor)

    any_alive = True
    while any_alive:
        any_alive = False
        for task_processor in task_processors:
            if task_processor.isAlive():
                any_alive = True

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

    def stop(self):
        self.stopping = True

    def run(self):
        if self.stopping:
            return

        rlms = RLMS("{}")
        dbg(' - %s lang: %s' % (self.laboratory_id, self.language))
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

PHET = register("PhET", ['1.0'], __name__)
PHET.add_global_periodic_task('Populating cache', populate_cache, hours = 23)

def main():
    rlms = RLMS("{}")
    t0 = time.time()
    laboratories = rlms.get_laboratories()
    tf = time.time()
    print len(laboratories), (tf - t0), "seconds"
    print
    print laboratories[:10]
    print
    # print rlms.reserve('http://phet.colorado.edu/en/simulation/density', 'tester', 'foo', '', '', '', '', locale = 'pt_ALL')
    for lab in laboratories[:5]:
        for lang in ('en', 'pt'):
            t0 = time.time()
            print rlms.reserve(lab.laboratory_id, 'tester', 'foo', '', '', '', '', locale = lang)
            tf = time.time()
            print tf - t0, "seconds"
    

if __name__ == '__main__':
    main()
