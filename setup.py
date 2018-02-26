#!/usr/bin/env python

from distutils.core import setup

setup(
    name='rdb',
    version='1.0',
    author='Ryan Petrello',
    author_email='ryan@ryanpetrello.com',
    install_requires='pygments',
    py_modules=['rdb'],
    entry_points={
        'console_scripts': [
            'rdb-listen = rdb:listen'
        ]
    }
 )
