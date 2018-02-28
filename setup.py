#!/usr/bin/env python

from setuptools import setup
import unittest


def suite():
    test_loader = unittest.TestLoader()
    test_suite = test_loader.discover('tests', pattern='test_*.py')
    return test_suite


setup(
    name='sdb',
    version='1.4',
    author='Ryan Petrello',
    author_email='ryan@ryanpetrello.com',
    url='https://github.com/ryanpetrello/sdb',
    description='A socket-based remote debugger for Python',
    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'Environment :: Console',
        'Intended Audience :: Developers',
        'Intended Audience :: System Administrators',
        'License :: OSI Approved :: BSD License',
        'Operating System :: MacOS :: MacOS X',
        'Operating System :: Microsoft :: Windows',
        'Operating System :: POSIX',
        'Programming Language :: Python',
        'Programming Language :: Python :: 2',
        'Programming Language :: Python :: 3',
    ],
    install_requires='pygments',
    test_suite='setup.suite',
    py_modules=['sdb'],
    entry_points={
        'console_scripts': [
            'sdb-listen = sdb:listen'
        ]
    }
 )
