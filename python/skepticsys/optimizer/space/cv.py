from hyperopt import hp
from hyperopt.pyll.base import scope

# parent imports
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))) # 2 levels up
from cross_validation import WindowSplit
sys.path.pop(0)
# end parent imports

def get_cv_space(
    params={}
    , do_transforms=False
):
    if do_transforms:
        transform_space = {**default_transform_cv}
    else:
        transform_space = {}

    space = {**default_cv, **transform_space, **params}

    return {
        'cv__params': space
    }

default_cv = {
    'train_size': scope.int(hp.quniform('cv__train_size', 480, 6000, 1)) # 960
    , 'train_sliding': hp.choice('cv__train_sliding', [False, True])

    # master
    # , 'master': True
    , 'test_size': scope.int(hp.quniform('cv__test_size', 60, 250, 1))
    , 'test_n': 4 #scope.int(hp.quniform('cv__test_n', 1, 11, 1))

    , 'verify_factor': [0.5] #,0.25] #hp.choice('cv__verify_factor', [1, 0.5, 0.25])

    , 'target_gap': True

    , 'doing_transforms': False
    , 'transforms': None
}

default_transform_cv = {
    'doing_transforms': True
    , 'transforms': hp.choice('cv__transforms', [
        [
            {
                'calibration': True
                , 'method': hp.choice('cv__cal__method', ['sigmoid','isotonic']) #,'convex','beta'])
                , 'test_size': scope.int(hp.quniform('cv__cal__test_size', 60, 250, 1)) # 120
                , 'test_n': scope.int(hp.quniform('cv__cal__test_n', 1, 5, 1)) # 4
            }
        ]

        , [
            {
                'threshold': True
                , 'method': hp.choice('cv__thr__method', ['youden','roc'])                
                , 'test_size': scope.int(hp.quniform('cv__thr__test_size', 60, 250, 1)) # 120
                , 'test_n': scope.int(hp.quniform('cv__thr__test_n', 1, 5, 1)) # 4
            }
        ]

        ,
        [
            {
                'cutoff': True
                , 'test_size': scope.int(hp.quniform('cv__cut__test_size', 60, 250, 1)) # 120
                , 'test_n': scope.int(hp.quniform('cv__cut__test_n', 1, 5, 1)) # 4
            }
        ]

        , [
            {
                'calibration': True
                , 'method': hp.choice('cv__cal_cut__cal__method', ['sigmoid','isotonic']) #,'convex','beta'])
                , 'test_size': scope.int(hp.quniform('cv__cal_cut__cal__test_size', 60, 250, 1)) # 120
                , 'test_n': scope.int(hp.quniform('cv__cal_cut__cal__test_n', 1, 5, 1)) # 4
            }
            , {
                'cutoff': True
                , 'test_size': scope.int(hp.quniform('cv__cal_cut__cut__test_size', 60, 250, 1)) # 120
                , 'test_n': scope.int(hp.quniform('cv__cal_cut__cut__test_n', 1, 5, 1)) # 4
            }
        ]
    ])
}