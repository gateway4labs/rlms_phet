PhET plug-in
=====================

The `LabManager <http://github.com/gateway4labs/labmanager/>`_ provides an API for
supporting more Remote Laboratory Management Systems (RLMS). This project is the
implementation for the `PhET
http://http://phet.colorado.edu/>`_ virtual laboratories.

Usage
-----

First install the module::

  $ pip install git+https://github.com/gateway4labs/rlms_phet.git

Then add it in the LabManager's ``config.py``::

  RLMS = ['phet', ... ]

Profit!
