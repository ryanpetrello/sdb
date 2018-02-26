#!/usr/bin/env python

from distutils.core import setup

with open('README.rst') as f:
    DESCRIPTION = f.read()

setup(
    name='sdb',
    version='1.0',
    author='Ryan Petrello',
    author_email='ryan@ryanpetrello.com',
    description=DESCRIPTION,
    install_requires='pygments',
    py_modules=['sdb'],
    entry_points={
        'console_scripts': [
            'sdb-listen = sdb:listen'
        ]
    }
 )
