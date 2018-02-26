#!/usr/bin/env python

from distutils.core import setup

with open('README.rst') as f:
    DESCRIPTION = f.read()

setup(
    name='sdb',
    version='1.3',
    author='Ryan Petrello',
    author_email='ryan@ryanpetrello.com',
    url='https://github.com/ryanpetrello/sdb',
    description='A socket-based remote debugger for Python',
    long_description=DESCRIPTION,
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
    py_modules=['sdb'],
    entry_points={
        'console_scripts': [
            'sdb-listen = sdb:listen'
        ]
    }
 )
