import numpy as np
import pandas as pd
import hyperopt as hp
from imblearn.pipeline import make_pipeline
from collections import OrderedDict, Iterable
import copy
from xgboost import XGBClassifier
import random
import sklearn.metrics as skm
from sklearn.utils.sparsefuncs import count_nonzero
import backtrader as bt
import backtrader.analyzers as bta
import uuid
import datetime
import traceback
import pprint
from sklearn.base import clone
import math

# parent submodules
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cross_validation import SingleSplit, WindowSplit
from estimators import ClassifierCV, CalibratedClassifierCV, ThresholdClassifierCV, CutoffClassifierCV
from metrics import BacktraderScorer
from trading import SeriesStrategy, BasicTradeStats
from preprocessors import IndicatorTransformer, CopyTransformer, DeltaTransformer, ShiftTransformer, NanSampler
from datasets import load_prices, get_target
from pipeline import make_union
from utils import arr_to_datetime
sys.path.pop(0)
# end parent submodules

def do_candidate(params):
    try:
        return do_fit_predict(params)
    except Exception as e:
        # https://stackoverflow.com/a/1278740
        #raise e
        traceback.print_exc()
        fname = os.path.split(sys.exc_info()[-1].tb_frame.f_code.co_filename)[1]
        msg = '%s, %s, %s | %s' % (sys.exc_info()[0].__name__, fname, sys.exc_info()[-1].tb_lineno, str(e))
        out = fail_trial('Exception: %s'%(msg))
        #print(out)
        return out

def fail_trial(msg, **data):
    out = {'status': hp.STATUS_FAIL, 'id': str(uuid.uuid4()), 'date': str(datetime.datetime.now()), 'msg': msg}
    for k in data:
        out[k] = data[k]
    print('Trial failed: {}'.format(msg))
    return out

def do_fit_predict(params):
    #setup
    super_threshold_level = 0.7
    super_threshold_field = 'accuracy'
    nans = NanSampler(drop_inf=False)
    print('='*48)

    # load prices
    prices, target = do_data(params['data__params'], params['cv__params'])
    prices_trade = prices.copy()
    target_trade = target.copy()

    # get cv model and check validity
    prices_model, target_model = nans.sample(prices, target)

    # get cv
    cv = get_cv(prices_model, params['data__params'], params['cv__params'])
    cv_base = get_cv(prices_model, params['data__params'], params['cv__params'], base_only=True)
    cv_verify = get_cv(prices_model, params['data__params'], params['cv__params'], do_verify=True)

    ##### start cv logging #####
    print('CV parameters')
    pprint.pprint(params['cv__params'], indent=4)
    for i, cv_unit in enumerate(cv):
        print('CV {}: {}'.format(i, str(cv_unit)))
    for i, cv_unit in enumerate(cv_verify):
        for j, cv_subunit in enumerate(cv_unit):
            print('Verify CV {}-{}: {}'.format(i, j, str(cv_subunit)))

    print('Sample_len query parameters')
    pprint.pprint(get_sample_len(params['data__params'], params['cv__params']))

    print('Price size: {}{}'.format(len(prices_model)
        , ' | From {} to {}'.format(prices_model.index[0], prices_model.index[-1]) if isinstance(prices_model, pd.DataFrame) else ''
    ))
    ##### end logging #####

    ### todo: CV model

    # do indicators
    print('Doing indicators') ##############
    indi_pipeline = do_indicators(**params['indicator__params'])
    if not bool(indi_pipeline):
        return fail_trial('Indicator pipeline: No transformers')
    prices_indi = indi_pipeline.transform(prices_model)
    # drop nan
    prices_indi, target_indi = nans.sample(prices_indi, target_model)

    if len(prices_indi) == 0:
        return fail_trial('Nan pipeline: No prices exist after transformation', shape=prices_indi.shape)

    # make all column names unique
    dup_cols = prices_indi.columns.get_duplicates()
    if len(dup_cols) > 0:
        dups = prices_indi.columns[prices_indi.columns.isin(dup_cols)]
        dup_vals = prices_indi.loc[:,prices_indi.columns.isin(dup_cols)]
        unq_vals = prices_indi.loc[:,~prices_indi.columns.isin(dup_cols)]
        fixed_dups = dups.map(lambda x: x+'__'+str(random.uniform(0,1)))
        dup_vals.columns = fixed_dups
        prices_indi = pd.concat([unq_vals, dup_vals], axis=1)

    # do classifier
    print('Doing classifier') ##############
    print('Prices shape: {}{}'.format(prices_indi.shape if hasattr(prices_indi, 'shape') else None
        , ' | From {} to {}'.format(prices_indi.index[0], prices_indi.index[-1]) if isinstance(prices_indi, pd.DataFrame) else ''
    ))

    ### todo: split CV

    clf = do_classifier(**params['classifier__params'])

    score_base = do_cv_fit_score(prices_model, target_model, prices_indi, target_indi, prices_trade, target_trade
                                 , cv_base, params, clf_method=do_classifier_transforms
                                 , base_clf=clf, cv_list=cv_base, cv_params=params['cv__params'], base_only=True 
                                 )

    if score_base['status'] == hp.STATUS_FAIL:
        return score_base
    elif True or score_base[super_threshold_field] >= super_threshold_level:
        print('Base %s: %s\nDoing verification...' % (super_threshold_field, score_base[super_threshold_field]))

        out = {
            'status': score_base['status']
            , 'loss': score_base['loss']
            , 'base': score_base
            , 'trans': None
            , 'verify': []
        }

        # verify
        # todo: this shouldn't be a list
        for cv_subverify in cv_verify:
            clf_verify = do_cv_fit(prices_model, target_model, prices_indi, target_indi, prices_trade, target_trade
                                   , cv_subverify, params, clf_method=do_classifier_transforms
                                   , base_clf=clf, cv_list=cv_subverify, cv_params=params['cv__params'], base_only=True
                                   )
            if isinstance(clf_verify, dict) and clf_verify['status'] == hp.STATUS_FAIL:
                return clf_verify
            score_verify = do_score(clf_verify, params, prices_indi, prices_trade)
            out['verify'].append(score_verify)

        # optimize params
        # if params['cv__params']['transforms'] is not None and len(params['cv__params']['transforms']) > 0:
            # score_trans = do_cv_fit(prices_model, target_model, prices_indi, target_indi, cv, params, clf_method=do_classifier_transforms
            #                        , base_clf=clf, cv_list=cv, cv_params=params['cv__params'], base_only=True 
            #                        )
            # clf_trans.fit(prices_indi, target)
            # score_trans = do_score(clf_trans, params, prices_indi, prices_trade)

            # out = {
            #     'base': score_base
            #     , 'trans': score_trans
            # }
            # if score_trans['loss'] < score_base['loss']:
            #     out['status'] = score_trans['status']
            #     out['loss'] = score_trans['loss']
            # else:
            #     out['status'] = score_base['status']
            #     out['loss'] = score_base['loss']

            # print('Trans accuracy: %s' % score_trans['accuracy'])
    else:
        print('Base %s: %s\nFinishing...' % (super_threshold_field, score_base[super_threshold_field]))
        out = {
            'status': score_base['status']
            , 'loss': score_base['loss']
            , 'base': score_base
            , 'trans': None
        }
    
    # add metadata
    out['meta'] = {
        'data': params['data__params']
        , 'cv': params['cv__params']
    }

    # score
    pprint.pprint(out)
    return out

def do_cv_fit(prices_model, target_model, prices_indi, target_indi, prices_trade, target_trade, cv, params, clf_method, **clf_params):
    cv_model = list(cv[-1].split(prices_model)) # concerned only with the lastmost CV
    for i, (train, test) in enumerate(cv_model):
        if len(train) == 0 or len(test) == 0:
            return fail_trial('CV invalid: set size is 0', train_len=len(train), test_len=len(test))
        else:
            print('Model CV Split {} freqs: {}{}'.format(i, target_model.iloc[test].value_counts().to_dict()
                , ' | From {} to {}'.format(target_model.iloc[test].index[0], target_model.iloc[test].index[-1]) if isinstance(target_model, pd.Series) else ''
            ))
    
    # split CV
    cv_split = list(cv[-1].split(prices_indi)) # concerned only with the lastmost CV
    ### TODO ### More sophisticated CV model checking
    if len(cv_split) != len(cv_model):
        return fail_trial('CV invalid: %s does not match model split count %s' % (len(cv_split), len(cv_model)), split_len=len(cv_split), model_len=len(cv_model))

    fail_reason = {}
    def check_split_model(X_train, y_train, X_test, y_test, i, cv_model_test):
        # validate CV
        train_model, test_model = cv_model_test[i][0], cv_model_test[i][1]

        print('CV Split {} size: {}, {}'.format(i, len(X_train), len(X_test))) ##############
        if len(X_train) == 0 or len(X_test) == 0:
            for k, v in fail_trial('CV Split invalid: set size is 0', train_len=len(X_train), test_len=len(X_test)).items():
                fail_reason[k] = v
            return False

        if len(X_test) != len(test_model):
            for k, v in fail_trial('CV Split invalid: test len does not match model', test_len=len(X_test), model_len=len(test_model)).items():
                fail_reason[k] = v
            return False

        # count CV frequencies
        y_model = target_trade.iloc[test_model]
        test_counts, model_counts = {k: v for k, v in zip(*[x.tolist() for x in np.unique(y_test, return_counts=True)])}, {k: v for k, v in zip(*[x.tolist() for x in np.unique(y_model, return_counts=True)])}
        print('CV Split {} freqs: {} | Model freqs: {}'.format(i, test_counts, model_counts))
        if test_counts != model_counts:
            for k, v in fail_trial('CV Split invalid: test freqs do not match model', test_freqs=test_counts, model_freqs=model_counts).items():
                fail_reason[k] = v
            return False
        return True

    clf_params['prefit_callback'] = check_split_model
    clf_params['prefit_params'] = {'cv_model_test':cv_model}

    clf = clf_method(**clf_params)
    try:
        clf.fit(prices_indi, target_indi)
        return clf
    except Exception as e:
        traceback.print_exc()
        if len(fail_reason) > 0:
            return fail_reason
        else:
            return fail_trial('ClassifierCV error: %s'%(str(e)))

def do_cv_fit_score(prices_model, target_model, prices_indi, target_indi, prices_trade, target_trade, cv, params, clf_method, **clf_params):
    clf = do_cv_fit(prices_model=prices_model, target_model=target_model, prices_indi=prices_indi, target_indi=target_indi
                    , prices_trade=prices_trade, target_trade=target_trade, cv=cv, params=params
                    , clf_method=clf_method, **clf_params
                    )
    if isinstance(clf, dict) and clf['status'] == hp.STATUS_FAIL:
        return clf
    else:
        return do_score(clf, params, prices_indi, prices_trade)

def do_score(clf_cv, params, prices, prices_trade):
    # score
    print('Scoring') ##############
    agg_method = 'concatenate'

    acc = clf_cv.score_cv(skm.accuracy_score, aggregate=agg_method)
    precision, recall, fscore, support = clf_cv.score_cv(skm.precision_recall_fscore_support, aggregate=agg_method)
    brier = clf_cv.score_cv(skm.brier_score_loss, aggregate=agg_method, proba_positive=True)
    logloss = clf_cv.score_cv(skm.log_loss, aggregate=agg_method)

    # prep backtrader score
    end_offset = abs(params['data__params']['end_target']) + abs(params['data__params']['start_target'])

    y_test = clf_cv.y_true
    y_pred = clf_cv.y_pred

    try:
        start_loc = prices_trade.index.get_loc(y_test.index[0])
    except KeyError:
        start_loc = 0
    try:
        end_loc = min(prices_trade.index.get_loc(y_test.index[-1])+end_offset, len(prices_trade)-1)
    except KeyError:
        end_loc = len(prices_trade)-1

    y_prices = prices_trade.iloc[int(start_loc):int(end_loc+1),:]

    pnl, trade_stats = do_backtest(y_pred, y_test, y_prices, expirebars=abs(params['data__params']['end_target'])-abs(params['data__params']['start_target']))
        # issue #16: expirebars appears to be correct, because end_target-start_target is the proper bar
        # expiry. See also SeriesStrategy, which needs to check expirebars-1 due to its counting.

    # compile scores
    loss = logloss #-acc # brier # -pnl

    out = {
        'status': hp.STATUS_OK
        , 'loss': loss
        , 'id': str(uuid.uuid4())
        , 'date': str(datetime.datetime.now())
        , 'trade_stats': trade_stats
        , 'pnl': pnl
        , 'brier': brier
        , 'logloss': logloss
        , 'accuracy': acc
        , 'precision': list(precision if precision is not None else [])
        , 'recall': list(recall if recall is not None else [])
        #, 'fscore': list(fscore if fscore is not None else [])
        , 'support': list(support if support is not None else [])
        , 'shape': prices.shape if hasattr(prices,'shape') else 'No shape? ' + str(type(prices))
    }
    return out

####################################
# Data and CV
####################################

def doing_verify(cv_params):
    return len(cv_params['verify_factor']) > 0 if isinstance(cv_params['verify_factor'], Iterable) else cv_params['verify_factor'] > 0 if cv_params['verify_factor'] is not None else False

def get_verify_n(test_n, factor):
    return math.ceil(test_n * factor)

def get_transforms(cv_params, base_only=False):
    # add master transform to end of list
    transforms = list(cv_params['transforms']) if not base_only and cv_params['transforms'] is not None and len(cv_params['transforms']) > 0 else []
    transforms.append({
        'master': True
        , 'test_size': cv_params['test_size']
        , 'test_n': cv_params['test_n']
    })
    return transforms

def get_split_sizes(transforms, verify_factors=[1], separate_verify=False):
    # todo: separate verify's test split needs to reflect start/end index;
    # train splits must be rolled into nominal train split
    total_test_size, total_verify_size = 0, 0
    for transform in transforms:
        total_test_size += transform['test_size'] * transform['test_n']
        if 'master' in transform:
            total_verify_size += transform['test_size'] * get_verify_n(transform['test_n'], max(verify_factors))
        else:
            if separate_verify:
                total_verify_size += transform['test_size'] * transform['test_n']
    return total_test_size, total_verify_size

def get_sample_len(data_params, cv_params):
    transforms = get_transforms(cv_params)

    # get base train size
    base_train_size = cv_params['train_size']
    
    if doing_verify(cv_params):
        # add test size to nominal train size, because we're doing verification
        # make verify size the nominal test size
        total_test_size, total_verify_size = get_split_sizes(transforms)
        base_train_size += total_test_size
        base_test_size = total_verify_size
    else:
        base_test_size = cv_params['test_n'] * cv_params['test_size']

    return {
        'train': base_train_size
        , 'test': base_test_size
        , 'target': int(abs(data_params['end_target']) + abs(data_params['start_target']))
    }

def do_data(data_params, cv_params):
    sample_len = get_sample_len(data_params, cv_params) # all needed data points for train test and target
    prices = load_prices(
        data_params['instrument']
        , data_params['granularity']
        , start_index=data_params['start_index']
        , end_index=data_params['end_index']
        , source=data_params['source']
        , sample_len=sample_len
        , dir=data_params['dir']
        , from_test=True
    )
    target = get_target(prices, data_params['end_target'], start_offset=data_params['start_target'])
    return prices, target

def get_cv(prices, data_params, cv_params, base_only=False, do_verify=False):
    if 'cv' in cv_params:
        return cv_params['cv'](**cv_params['params'])
    elif 'single_split' in cv_params:
        return SingleSplit(test_size=cv_params['single_split'])

    # else, construct chained WindowSplit CV
    # base params go last
    transforms = get_transforms(cv_params)

    verify_cv = []
    verify_factors = [1] #if not do_verify else cv_params['verify_factor'] if isinstance(cv_params['verify_factor'], Iterable) else [cv_params['verify_factor']] if cv_params['verify_factor'] is not None else [1]

    total_test_size, total_verify_size = get_split_sizes(transforms, verify_factors=verify_factors)

    # assume that data bounds accurately encompass verify_n*test_size + test_n*test_size
    data_test_end = len(prices)-total_verify_size-1 # non-inclusive, making this the inclusive start of verify master split
    data_test_start = data_test_end-total_test_size #-1 # first index of first test split
    data_verify_start = data_test_end-sum([transform['test_size']*transform['test_n'] 
                                           for transform in transforms if 'master' not in transform]
                                          )
        # this is different from verify master split, as pre-transforms must run before master split, unless separate_verify is true (todo)

    for verify_factor in verify_factors:
        transform_cv = []
        prior_train_size = cv_params['train_size']
        prior_test_size = 0
        for transform in transforms:
            # Window size calculation: [train = (sum(test len) + train len)] + sum(test len)
            if not do_verify:
                current_test_size = transform['test_size'] * transform['test_n']
                initial_test_index = data_test_start + prior_test_size
                final_index = initial_test_index + current_test_size
            else:
                if 'master' in transform:
                    current_test_size = transform['test_size'] * get_verify_n(transform['test_n'], verify_factor)
                else:
                    current_test_size = transform['test_size'] * transform['test_n']
                initial_test_index = data_verify_start + prior_test_size # inclusive start of verify split
                final_index = initial_test_index + current_test_size

            train_size = prior_train_size
            args = {
                'test_size': abs(transform['test_size'])
                , 'step_size': abs(transform['test_size'])
                , 'initial_test_index': initial_test_index-len(prices)
                , 'final_index': final_index-len(prices) #- 1
                    # todo: problem: final_index is inclusive, so initial_test_index and final_index are not
                    # mutually exclusive between splits. Adjusting this messes up the split length.
            }
            if cv_params['train_sliding']:
                args['initial_train_index'] = 0
                if base_only and 'master' in transform: # HACK: change train length to base; all else is correct
                    args['sliding_size'] = cv_params['train_size']
                else:
                    args['sliding_size'] = train_size
            else:
                if base_only and 'master' in transform: # HACK: change train length to base; all else is correct
                    args['initial_train_index'] = min(0, args['initial_test_index']-cv_params['train_size'])
                else:
                    args['initial_train_index'] = min(0, args['initial_test_index']-train_size)
                args['sliding_size'] = None
            
            transform_cv.append(WindowSplit(**args))
            prior_train_size += current_test_size
            prior_test_size += current_test_size
        verify_cv.append(transform_cv)

    if not do_verify and len(verify_cv) > 0:
        if base_only:
            return [verify_cv[0][-1]]
        else:
            return verify_cv[0]
    else:
        if base_only:
            return [[unit[-1]] for unit in verify_cv]
        else:
            return verify_cv

####################################
# Indicators
####################################

def do_indicators(
    **indi_params
):
    master_union = []
    for indi in indi_params:
        if not bool(indi_params[indi]):
            continue

        main_params = indi_params[indi].pop('_params', None)
    
        for subindi in indi_params[indi]:
            if not bool(indi_params[indi][subindi]):
                continue
            else:
                trans_pipe = []

            # do ma for the subindi pipeline
            ### HACK ### 
            # Params dict copy is needed to fix an error where IndicatorTransformer stores an empty dict
            # instead of the params
            if '_ma' in indi_params[indi][subindi] and bool(indi_params[indi][subindi]['_ma']):
                ma_params = indi_params[indi][subindi]['_ma']
                pre = ma_params.pop('_pre', None)
                if pre:
                    trans_pipe.append(IndicatorTransformer(**{'ma__pre': {**ma_params}}))
                    trans_pipe.append(IndicatorTransformer(**{indi: {**main_params}}))
                else:
                    trans_pipe.append(IndicatorTransformer(**{indi: {**main_params}}))
                    trans_pipe.append(IndicatorTransformer(**{'ma__post': {**ma_params}}))
            else:
                trans_pipe.append(IndicatorTransformer(**{indi: {**main_params}}))

            # do delta child pipelines
            if '_delta' in indi_params[indi][subindi] and bool(indi_params[indi][subindi]['_delta']):
                delta_params = indi_params[indi][subindi]['_delta']
                base = delta_params.pop('_base', None)
                for inst in delta_params:
                    if not bool(delta_params[inst]):
                        continue
                    ma_params = delta_params[inst].pop('_ma', None)
                    shift_params = delta_params[inst].pop('_shift', None)

                    # copy current pipe and apply transformer
                    inst_pipe = copy.deepcopy(trans_pipe)
                    inst_pipe.append(DeltaTransformer(**delta_params[inst]))

                    # if ma is specified, do that
                    if bool(ma_params):
                        ma_params.pop('_pre', None)
                        inst_pipe.append(IndicatorTransformer(**{'ma__delta':{**ma_params}}))

                    # if shift is specified, do that
                    if bool(shift_params):
                        inst_pipe.append(ShiftTransformer(**shift_params))

                    if len(inst_pipe) == 1:
                        master_union.append(inst_pipe[0])
                    elif len(inst_pipe) > 1:
                        master_union.append(make_pipeline(*inst_pipe))
                    # else, don't append anything, continue
                # if _base exists and is false, don't construct the subindi pipeline (non-delta)
                if base is not None and not base:
                    continue
                # else, continue constructing the subindi pipeline

            # do shift for the subindi pipeline
            if '_shift' in indi_params[indi][subindi] and bool(indi_params[indi][subindi]['_shift']):
                trans_pipe.append(ShiftTransformer(**indi_params[indi][subindi]['_shift'], keep_features=True))

            if len(trans_pipe) == 1:
                master_union.append(trans_pipe[0])
            elif len(trans_pipe) > 1:
                master_union.append(make_pipeline(*trans_pipe))
            # else, don't append anything, continue
    
    if len(master_union) == 1:
        return master_union[0]
    elif len(master_union) > 1:
        return make_union(*master_union)
    else:
        return None

####################################
# Classifiers and Transforms
####################################

def do_classifier(
    **classifier_params
):
    master_pieces = {}
    for _, pipe_params in classifier_params.items():
        if not bool(pipe_params):
            continue

        order_base = pipe_params.pop('_order_base', 0.)
        order_factor = pipe_params.pop('_order_factor', 0.)
        order_key = float(order_base)*float(order_factor)
        while order_key in master_pieces:
            order_key += random.uniform(-1,1)

        pipe_pieces = []
        for clf_name, clf_params in pipe_params.items():
            if not bool(clf_params):
                continue

            if clf_name == 'xgb':
                pipe_pieces.append(XGBClassifier(**clf_params))

        if len(pipe_pieces) == 1:
            master_pieces[order_key] = pipe_pieces[0]
        elif len(pipe_pieces) > 1:
            master_pieces[order_key] = make_pipeline(*pipe_pieces)

    if len(master_pieces) == 1:
        return list(master_pieces.items())[0][-1] # value
    elif len(master_pieces) > 1:
        return make_pipeline(*[master_pieces[k] for k in sorted(list(master_pieces))])
    else:
        return None

def do_classifier_transforms(base_clf, cv_list, cv_params, base_only=False, **kwargs):
    # add master transform to end of list
    transforms = get_transforms(cv_params, base_only=base_only)

    clf = clone(base_clf)
    for i, transform in enumerate(transforms):
        if 'calibration' in transform:
            clf = CalibratedClassifierCV(base_estimator=clf, method=transform['method'], cv=cv_list[i])
        elif 'threshold' in transform:
            clf = ThresholdClassifierCV(base_estimator=clf, method=transform['method'], cv=cv_list[i])
        elif 'cutoff' in transform:
            clf = CutoffClassifierCV(base_estimator=clf, cv=cv_list[i])
        elif 'master' in transform:
            clf = ClassifierCV(base_estimator=clf, cv=cv_list[i], **kwargs)
        
    return clf

def do_backtest(
    y_pred, y_true, y_prices
    , expirebars = 1
    , initial_balance = 100000.
):
    y_prices = arr_to_datetime(y_prices, y_true=y_true)
    
    # make cerebro for BacktraderScorer
    cerebro = bt.Cerebro()
    cerebro.broker.set_cash(initial_balance)
    data = bt.feeds.PandasData(dataname=y_prices, openinterest=None)
    data2 = bt.feeds.PandasData(dataname=y_prices, openinterest=None)
    cerebro.adddata(data, name='LongFeed')
    cerebro.adddata(data2, name='ShortFeed')
    cerebro.addanalyzer(BasicTradeStats, useStandardPrint=True, useStandardDict=True, _name='BasicStats')
    # cerebro.addanalyzer(bta.SharpeRatio, timeframe=bt.TimeFrame.Minutes, compression=60, annualize=True, factor=6215, _name='SharpeRatio')
    #     # 6048 hours todo: standardize (6215?)
    # cerebro.addanalyzer(bta.VWR, timeframe=bt.TimeFrame.Minutes, compression=60, tann=6215, _name='VWR')

    # make scorers
    bts = BacktraderScorer(cerebro
        , SeriesStrategy, 'signals', strategy_kwargs={'tradeintervalbars':0, 'tradeexpirebars':expirebars, 'stake':1}
        , analyzer_name=['BasicStats'], analysis_key=[[]], score_name=['stats']
        # , analyzer_name=['BasicStats','SharpeRatio','VWR'], analysis_key=[[],None,None], score_name=['stats','sharpe','vwr']
        , initial_cash=initial_balance
    )

    # get score
    results = bts._bt_score(y_pred, y_true=y_true)
    results['stats']['won'].pop('streak')
    results['stats']['lost'].pop('streak')
    pnl = results['stats']['all']['pnl']['total']

    return pnl, results
