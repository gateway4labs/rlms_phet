# -*-*- encoding: utf-8 -*-*-

import re
import sys
from bs4 import BeautifulSoup
import json
import datetime
import uuid
import hashlib
import urllib2

from flask.ext.wtf import TextField, PasswordField, Required, URL, ValidationError

from labmanager.forms import AddForm
from labmanager.rlms import register, Laboratory
from labmanager.rlms.base import BaseRLMS, BaseFormCreator, Capabilities, Versions

def get_module(version):
    """get_module(version) -> proper module for that version

    Right now, a single version is supported, so this module itself will be returned.
    When compatibility is required, we may change this and import different modules.
    """
    # TODO: check version
    return sys.modules[__name__]

class PhETAddForm(AddForm):

    DEFAULT_URL = 'http://phet.colorado.edu/en/'
    DEFAULT_LOCATION = 'Colorado, USA'

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


def phet_url(url):
    return "http://phet.colorado.edu%s" % url

class RLMS(BaseRLMS):

    def __init__(self, configuration):
        self.configuration = json.loads(configuration or '{}')

    def get_version(self):
        return Versions.VERSION_1

    def get_capabilities(self):
        return [ Capabilities.WIDGET ]

    def get_laboratories(self, **kwargs):
        index_html = urllib2.urlopen(phet_url("/en/simulations/index")).read()
        soup = BeautifulSoup(index_html)
        
        laboratories = []

        for h2 in soup.find_all("h2"):
            parent_identifier = h2.parent.get('id')
            # Just checking that the format has not changed
            if parent_identifier and len(parent_identifier) == 1 and parent_identifier in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
                for link in h2.parent.find_all("a"):
                    link_href = phet_url(link.get('href'))
                    name = link.find("span").text
                    laboratories.append(Laboratory(name = name, laboratory_id = link_href, autoload = True))

        return laboratories

    def reserve(self, laboratory_id, username, institution, general_configuration_str, particular_configurations, request_payload, user_properties, *args, **kwargs):

        laboratory_html = urllib2.urlopen(laboratory_id).read()
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

        return {
            'reservation_id' : url,
            'load_url' : url
        }

    def load_widget(self, reservation_id, widget_name, **kwargs):
        return {
            'url' : reservation_id
        }

    def list_widgets(self, laboratory_id, **kwargs):
        default_widget = dict( name = 'default', description = 'Default widget' )
        return [ default_widget ]

register("PhET", ['1.0'], __name__)

def main():
    rlms = RLMS("{}")
    laboratories = rlms.get_laboratories()
    print len(laboratories)
    print
    print laboratories[:10]
    print
    for lab in laboratories[:5]:
        print rlms.reserve(lab.laboratory_id, 'tester', 'foo', '', '', '', '')
    

if __name__ == '__main__':
    main()
