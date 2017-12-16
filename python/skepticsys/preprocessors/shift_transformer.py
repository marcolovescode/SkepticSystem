import numpy as np
from sklearn.base import BaseEstimator, TransformerMixin, ClassifierMixin
from sklearn.utils import check_array
import collections
from scipy.ndimage.interpolation import shift

class ShiftTransformer(BaseEstimator, TransformerMixin):
    """Transformer for shifting input arrays.

    Parameters
    ----------
    estimator : object
        The base estimator from which the transformer is built.
    """

    def __init__(self, shift, fill_value=np.nan, keep_features=False):
        """Create a ShiftTransformer object.

        Parameters
        ----------
        shift: integer, list, or range
            Number of elements, or list of numbers, to shift by. Positive means shift to previous values, negative means shift to next values.
        """
        self.shift = shift if isinstance(shift, collections.Iterable) else [shift]
        self.fill_value = fill_value
        self.keep_features = keep_features

    def fit(self, X, y=None, **fit_params):
        """Fit the ShiftTransformer transformer.

        Parameters
        ----------
        X: array-like of shape (n_samples, n_features)
            The training input samples.
        y: array-like, shape (n_samples,)
            The target values (integers that correspond to classes in classification, real numbers in regression).
        fit_params:
            Other estimator-specific parameters.

        Returns
        -------
        self: object
            Returns a copy of the estimator
        """
        return self

    def transform(self, X):
        """Transform data by shifting features

        Parameters
        ----------
        X: numpy ndarray, {n_samples, n_components}
            New data, where n_samples is the number of samples and n_components is the number of components.

        Returns
        -------
        X_transformed: array-like, shape (n_samples, n_features + 1) or (n_samples, n_features + 1 + n_classes) for classifier with predict_proba attribute
            The transformed feature set.
        """
        shift = self.shift
        fill_value = self.fill_value
        is_pandas = isinstance(X, pd.DataFrame) or isinstance(X, pd.Series)

        if is_pandas:
            X_base = X.copy()
        else:
            X_base = np.copy(X)

        transformed_list = [X_base] if self.keep_features else []

        for shift_i in shift:
            transformed_list.append(
                self._shift_array(X_base, shift_i, fill_value=fill_value)
            )
        
        if is_pandas:
            X_transformed = pd.concat(transformed_list, axis=1)
        else:
            X_transformed = np.concatenate(transformed_list, axis=1)

        return X_transformed

    def _shift_array(self, arr, num, fill_value=np.nan):
        is_pandas = isinstance(arr, pd.DataFrame) or isinstance(arr, pd.Series)

        if is_pandas:
            result = arr.shift(num)
            if fill_value is not np.nan:
                if num > 0:
                    result.iloc[:min(num+1, len(result))] = fill_value
                elif num < 0:
                    result.iloc[max(len(result)+num, 0):] = fill_value
        else:
            result = shift(arr, shift=num, cval=fill_value, mode='constant')

        return result
    