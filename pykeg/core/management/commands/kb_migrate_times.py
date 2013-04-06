# Copyright 2010 Mike Wakerly <opensource@hoho.com>
#
# This file is part of the Pykeg package of the Kegbot project.
# For more information on Pykeg or Kegbot, see http://kegbot.org/
#
# Pykeg is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
#
# Pykeg is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Pykeg.  If not, see <http://www.gnu.org/licenses/>.

"""Migrate timestamps.

Prior to Kegbot 0.9.8, timestamps were stored in the database in localtime. This
tool adjusts these timestamps to UTC time.

You should only use this command if you are upgrading from a version of Kegbot
earlier than 0.9.8.  If all of your data was generated by Kegbot 0.9.8 or newer,
or if you have already run this command, you should NOT proceed.

WARNING: Back up your data before proceeding.
"""

HELP_TEXT = __doc__

import pytz
import sys

from django.db import transaction
from django.conf import settings
from django.core.management.base import CommandError
from django.core.management.base import NoArgsCommand

from django.utils import timezone
from pykeg.core import models

class Command(NoArgsCommand):
  help = u'Regenerates timestamps due to timezone conversion.'
  args = '<none>'

  def handle(self, **options):
    print HELP_TEXT
    confirm('Press Y to continue. ')

    print 'Please enter the SESSION ID for the LAST session you want to '
    print 'migrate.  All drinks UP TO AND INCLUDING this session will be '
    print 'migrated. '

    default_session = models.DrinkingSession.objects.all().order_by('-id')[0]
    lbl = '%s (%s)' % (default_session.id, default_session.start_time)

    print ''
    session_id = raw_input('Session id [%s]: ' % lbl).strip()
    print ''
    if not session_id:
      session_id = default_session.id
    else:
      session_id = int(session_id)

    session = models.DrinkingSession.objects.get(id=session_id)

    print ''
    print 'Your current settings.TIME_ZONE is: %s' % (settings.TIME_ZONE,)
    print 'Drinks will be migrated assuming they occurred in this zone. '
    print ''
    confirm('Correct time zone?')

    print ''
    print 'You selected session id %s.' % (session.id,)
    print ''
    print '   Current start time: %s' % timezone.localtime(session.start_time)
    print ' Corrected start time: %s' % timezone.localtime(convert(session.start_time))
    print '           Difference: %s' % (convert(session.start_time) - session.start_time)

    print ''
    confirm('Does this look correct?')
    print ''

    drinks = models.Drink.objects.filter(session_id__lte=session.id).order_by('id')
    print 'There are %s drinks up to and including this session.' % len(drinks)

    print ''
    confirm('Start migration?')
    print ''

    with transaction.commit_on_success():
      do_migrate(drinks)

      print ''
      print 'All items migrated.  Please spot check the report that just '
      print 'scrolled by.  If in doubt, abort now and no changes will be '
      print 'made.'
      print ''
      print 'WARNING: Pressing Y will permanently save adjusted times.'
      print ''
      confirm('Commit transaction?')
      print ''
      print 'Committing transaction..'
      print ''

    print 'Migration finished.'

def migrate(obj, attrs, errors):
  print '  %s' % str(obj)
  for attr in attrs:
    old = getattr(obj, attr)
    if old:
      try:
        new = convert(old)
      except pytz.exceptions.NonExistentTimeError, e:
        print '    - ERR: %s' % e
        errors.append((obj, attr, e))
        continue
      setattr(obj, attr, new)
      print '    - %s: %s -> %s' % (attr, old, new)
  obj.save()


def do_migrate(drinks):
  LT = timezone.localtime

  errors = []
  kegs = set()
  sessions = set()
  pictures = []
  for drink in drinks:
    migrate(drink, ['time'], errors)
    if drink.keg:
      kegs.add(drink.keg)
    sessions.add(drink.session)
    for p in drink.pictures.all().order_by('id'):
      pictures.append(p)

  print ''
  for picture in pictures:
    migrate(picture, ['time'], errors)

  print ''
  for session in sessions:
    migrate(session, ['start_time', 'end_time'], errors)

  print ''
  for keg in kegs:
    migrate(keg, ['start_time', 'end_time'], errors)

  if errors:
    print ''
    print 'ERROR: MIGRATION ABORTED'
    print ''
    print 'The following objects have impossibe/non-existent times.'
    print 'Please fix them manually and re-run.'
    print ''
    for obj, attr, e in errors:
      print '  %s: %s: %s' % (obj, attr, e)
    print ''
    raise ValueError('Invalid time(s) found.')


def convert(dt):
  return timezone.make_aware(timezone.make_naive(dt, pytz.UTC),
      pytz.timezone(settings.TIME_ZONE))


def confirm(prompt):
  val = raw_input('%s [y/N]: ' % prompt)
  if not val or val[0].lower() != 'y':
    raise ValueError('Aborted by user.')
  print ''
