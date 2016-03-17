import os
import os.path as p
from contextlib import contextmanager
from xml.dom import minidom
from werkzeug import secure_filename
from tmaps.extensions.database import db
from tmaps.model.decorators import (
    auto_create_directory, exec_func_after_insert,
    auto_remove_directory
)
from tmaps.model import CRUDMixin, HashIdModel
import tmlib.plate
import tmlib.experiment
import tmlib.cfg

# EXPERIMENT_ACCESS_LEVELS = (
#     'read',
#     'delete'
# )


    # access_level = db.Column(db.Enum(*EXPERIMENT_ACCESS_LEVELS,
    #                                  name='access_level'))

def _default_experiment_location(exp):
    from _user import User
    user = User.query.get(exp.owner_id)
    dirname = secure_filename('%d__%s' % (exp.id, exp.name))
    dirpath = p.join(user.experiments_location, dirname)
    return dirpath

def _get_tmlib_object(location, plate_format):
    cfg = tmlib.cfg.UserConfiguration(location, plate_format)
    return tmlib.experiment.Experiment(location, cfg)

def _channels_location(exp):
    # return _get_tmlib_object(exp.location, exp.plate_format).layers_dir
    return p.join(exp.location, 'channels')

def _plates_location(exp):
    return _get_tmlib_object(exp.location, exp.plate_format).plates_dir

def _plate_sources_location(exp):
    return _get_tmlib_object(exp.location, exp.plate_format).sources_dir

def _create_locations_if_necessary(mapper, connection, exp):
    if exp.location is None:
        exp_location = _default_experiment_location(exp)
        # Temp. set the location so that all the other location functions
        # work correctly. This still has to be persisted using SQL (see below).
        exp.location = exp_location
        if not p.exists(exp_location):
            os.mkdir(exp_location)
        else:
            print 'Warning: dir %s already exists.' % exp_location
        # exp.location = loc line won't
        # persists the location on the object.
        # If done directly via SQL it works.
        table = Experiment.__table__
        connection.execute(
            table.update()
                 .where(table.c.id == exp.id)
                 .values(location=exp_location))


SUPPORTED_MICROSCOPE_TYPES = ('cellvoyager', 'visiview')
EXPERIMENT_CREATION_STAGES = (
    'WAITING_FOR_UPLOAD',
    'UPLOADING',
    'WAITING_FOR_IMAGE_CONVERSION'
    'CONVERTING_IMAGES',
    'WAITING_FOR_PYRAMID_CREATION',
    'CREATING_PYRAMIDS',
    'DONE'
)

@exec_func_after_insert(_create_locations_if_necessary)
@auto_remove_directory(lambda e: e.location)
class Experiment(HashIdModel, CRUDMixin):

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), index=True)

    location = db.Column(db.String(600))
    description = db.Column(db.Text)

    microscope_type = \
        db.Column(db.String(50))
    creation_stage = \
        db.Column(db.String(50))
    plate_format = db.Column(db.Integer)

    owner_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_on = db.Column(db.DateTime, default=db.func.now())
    owner = db.relationship('User', backref='experiments')

    def __init__(self, name, owner, microscope_type, plate_format,
                 description='', location=None):
        self.name = name
        self.description = description
        self.owner_id = owner.id

        if location is not None:
            if not p.isabs(location):
                raise ValueError(
                    'The experiments location on the filesystem must be '
                    'an absolute path'
                )
            else:
                self.location = location
        else:
            self.location = None

        self.creation_stage = 'WAITING_FOR_UPLOAD'

        if not microscope_type in set(SUPPORTED_MICROSCOPE_TYPES):
            raise ValueError('Unsupported microscope type')
        else:
            self.microscope_type = microscope_type

        if not plate_format in tmlib.plate.Plate.SUPPORTED_PLATE_FORMATS:
            raise ValueError('Unsupported plate format')
        else:
            self.plate_format = plate_format

    @property
    def tmlib_object(self):
        return _get_tmlib_object(self.location, self.plate_format)

    @property
    def dataset_path(self):
        return p.join(self.location, 'data.h5')

    @property
    def has_dataset(self):
        return p.exists(self.dataset_path)

    @property
    def plates_location(self):
        return _plates_location(self)

    @property
    def plate_sources_location(self):
        return _plate_sources_location(self)

    def belongs_to(self, user):
        return self.owner == user

    @property
    @contextmanager
    def dataset(self):
        import h5py
        fpath = self.dataset_path
        f = h5py.File(fpath, 'r')
        yield f
        f.close()

    def __repr__(self):
        return '<Experiment %r>' % self.name

    @property
    def channels_location(self):
        return p.join(self.location, 'channels')

    @property
    def is_ready_for_image_conversion(self):
        all_plate_sources_ready = \
            all([pls.is_ready_for_processing for pls in self.plate_sources])
        is_ready = \
            self.creation_stage != 'CONVERTING_IMAGES' and all_plate_sources_ready
        return is_ready

    def as_dict(self):
        mapobject_info = []
        for t in self.mapobject_types:
            # TODO: Change to this as soon as DB is ready
            # features = [f.name for f in t.features]
            with self.dataset as d:
                feature_names = d['/objects/%s/features' % t.name].keys()
                features = [{'name': n} for n in feature_names]
            mapobject_info.append({
                'mapobject_type_name': t.name,
                'features': features
            })

        return {
            'id': self.hash,
            'name': self.name,
            'description': self.description,
            'owner': self.owner.name,
            'plate_format': self.plate_format,
            'microscope_type': self.microscope_type,
            'creation_stage': self.creation_stage,
            'channels': [ch.as_dict() for ch in self.channels],
            'plate_sources': [pl.as_dict() for pl in self.plate_sources],
            'mapobject_info': mapobject_info,
            'plates': [pl.as_dict() for pl in self.plates]
        }
