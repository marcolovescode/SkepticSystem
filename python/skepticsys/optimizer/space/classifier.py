from hyperopt import hp
from hyperopt.pyll.base import scope
from xgboost import XGBClassifier

def get_classifier_space(
    classifiers={}
):
    pipe_pool = {**default_classifiers,**classifiers}
    space = {}

    for pipe_name, pipe_cand in pipe_pool.items():
        if not bool(pipe_cand):
            continue

        pipe_space = {}
        for clf_type, clf_params in pipe_cand.items():
            if not bool(clf_params):
                continue
            else:
                pipe_space[clf_type] = clf_params

        if len(pipe_space) < 1:
            continue
        else:
            pipe_space['_order_factor'] = 0 if len(pipe_pool) < 2 else hp.uniform('clf__'+pipe_name+'__order_factor', 0, 1)
            space[pipe_name] = pipe_space if len(pipe_pool) < 2 else hp.choice('clf__'+pipe_name, [None, pipe_space])

    return {
        'classifier__params': space
    }

default_classifiers = {
    'xgbdefault': {
        '_order_base': -999
        , 'xgb': {
            'eval_metric': 'error' #hp.choice('xgb__eval_metric', ['error','auc','rmse','logloss'])
                # error allows for threshold different than 0.5 according to error@t. How to roll this in?
            , 'learning_rate': hp.choice('xgb__learning_rate', [0.05, 0.1, 0.2, 0.3]) # default=0.3, suggest=0.01,0.05,0.1
            , 'max_depth': scope.int(hp.quniform('xgb__max_depth', 1, 7, 1)) # 6
            , 'objective': 'binary:logistic' # when testing calibration, will need to detect when to replace this
            , 'silent': True
        }
    }
}