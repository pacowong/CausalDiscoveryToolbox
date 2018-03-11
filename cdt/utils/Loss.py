"""
Implementation of Losses
Author : Diviyan Kalainathan & Olivier Goudet
Date : 09/03/2017
"""


from .Settings import SETTINGS
import numpy as np
from scipy.stats import ttest_ind
import torch as th
from torch.autograd import Variable

bandwiths_gamma = [0.005, 0.05, 0.25, 0.5, 1, 5, 50]

if SETTINGS.tensorflow_is_available:
    import tensorflow as tf

    def MMD_loss_tf(xy_true, xy_pred, kernel="RBF"):

        N, _ = xy_pred.get_shape().as_list()

        if(kernel == "GMM"):

            X = tf.concat([xy_pred, xy_true], 0)

            X_unstacked = tf.unstack(X, axis=1)
            list_unstacked = []
            for t in X_unstacked:
                list_unstacked.append(tf.reshape(t, [2 * N, 1]))

            X = tf.concat([i for c in [[tf.abs(tf.maximum(j, 0)), tf.abs(
                tf.minimum(j, 0))] for j in list_unstacked] for i in c], axis=1)
            X = tf.expand_dims(X, 1)
            X_t = tf.transpose(X, perm=[1, 0, 2])

            s1 = tf.constant(1.0 / N, shape=[N, 1])
            s2 = -tf.constant(1.0 / N, shape=[N, 1])
            s = tf.concat([s1, s2], 0)
            S = tf.matmul(s, tf.transpose(s))

            loss = tf.reduce_sum(S * tf.div(tf.reduce_sum(tf.minimum(X, X_t),
                                                          axis=2), tf.reduce_sum(tf.maximum(X, X_t), axis=2)))

        elif(kernel == "RBF"):

            X = tf.concat([xy_pred, xy_true], 0)
            XX = tf.matmul(X, tf.transpose(X))
            X2 = tf.reduce_sum(X * X, 1, keep_dims=True)
            exponent = -2 * XX + X2 + tf.transpose(X2)

            s1 = tf.constant(1.0 / N, shape=[N, 1])
            s2 = -tf.constant(1.0 / N, shape=[N, 1])
            s = tf.concat([s1, s2], 0)
            S = tf.matmul(s, tf.transpose(s))

            loss = 0

            for i in bandwiths_gamma:
                kernel_val = tf.exp(-i * exponent)
                loss += tf.reduce_sum(S * kernel_val)

        return loss

    def rp(k, s, d):
        return tf.transpose(tf.concat([tf.concat([2 * si * tf.random_normal([k, d], mean=0, stddev=1) for si in s], axis=0),
                                       tf.random_uniform([k * len(s), 1], minval=0, maxval=2 * np.pi)], axis=1))

    def f1(x, wz, N):
        ones = tf.ones((N, 1))
        x_ones = tf.concat([x, ones], axis=1)
        mult = tf.matmul(x_ones, wz)

        return tf.cos(mult)

    def Fourier_MMD_Loss_tf(xy_true, xy_pred, nb_vectors_approx_MMD):
        N, nDim = xy_pred.get_shape().as_list()

        wz = rp(nb_vectors_approx_MMD, bandwiths_gamma, nDim)

        e1 = tf.sqrt(2 / nb_vectors_approx_MMD) * \
            tf.reduce_mean(f1(xy_true, wz, N), axis=0)
        e2 = tf.sqrt(2 / nb_vectors_approx_MMD) * \
            tf.reduce_mean(f1(xy_pred, wz, N), axis=0)

        return tf.reduce_sum((e1 - e2) ** 2)

    def MomentMatchingLoss_tf(xy_true, xy_pred, nb_moment=1):
        """ k-moments loss, k being a parameter. These moments are raw moments and not normalized

        """
        loss = 0
        for i in range(1, nb_moment):
            mean_pred = tf.reduce_mean(xy_pred ** i, 0)
            mean_true = tf.reduce_mean(xy_true ** i, 0)
            loss += tf.sqrt(tf.reduce_sum((mean_true - mean_pred) ** 2))  # L2

        return loss


class TTestCriterion(object):
    def __init__(self, max_iter, runs_per_iter, threshold=0.01):
        super(TTestCriterion, self).__init__()
        self.threshold = threshold
        self.max_iter = max_iter
        self.runs_per_iter = runs_per_iter
        self.iter = 0
        self.p_value = np.nan

    def loop(self, xy, yx):
        if len(xy) > 0:
            self.iter += self.runs_per_iter
        if self.iter < 2:
            return True
        t_test, self.p_value = ttest_ind(xy, yx, equal_var=False)
        if self.p_value > self.threshold and self.iter < self.max_iter:
            return True
        else:
            return False


class MMD_loss_th(th.nn.Module):
    def __init__(self, input_size, cuda=False):
        super(MMD_loss_th, self).__init__()
        self.bandwiths = [0.01, 0.1, 1, 5, 20, 50, 100]
        self.cuda = cuda
        if self.cuda:
            s1 = th.cuda.FloatTensor(input_size, 1).fill_(1)
            s2 = s1.clone()
            s = th.cat([s1.div(input_size),
                        s2.div(-input_size)], 0)

        else:
            s = th.cat([(th.ones([input_size, 1])).div(input_size),
                        (th.ones([input_size, 1])).div(-input_size)], 0)

        self.S = s.mm(s.t())
        self.S = Variable(self.S)

    def forward(self, var_input, var_pred, var_true=None):

        # MMD Loss
        if var_true is None:
            X = th.cat([var_input, var_pred], 0)
        else:
            X = th.cat([th.cat([var_input, var_pred], 1),
                        th.cat([var_input, var_true], 1)], 0)
        # dot product between all combinations of rows in 'X'
        XX = X.mm(X.t())

        # dot product of rows with themselves
        X2 = (X.mul(X)).sum(dim=1)

        # exponent entries of the RBF kernel (without the sigma) for each
        # combination of the rows in 'X'
        # -0.5 * (x^Tx - 2*x^Ty + y^Ty)
        exponent = XX.sub((X2.mul(0.5)).expand_as(XX)) - \
            (((X2.t()).mul(0.5)).expand_as(XX))

        if self.cuda:
            lossMMD = Variable(th.cuda.FloatTensor([0]))
        else:
            lossMMD = Variable(th.zeros(1))
        for i in range(len(self.bandwiths)):
            kernel_val = exponent.mul(1. / self.bandwiths[i]).exp()
            lossMMD.add_((self.S.mul(kernel_val)).sum())

        return lossMMD.sqrt()


class MomentMatchingLoss_th(th.nn.Module):
    """ k-moments loss, k being a parameter. These moments are raw moments and not normalized

    """

    def __init__(self, n_moments=1):
        """ Initialize the loss model

        :param n_moments: number of moments
        """
        super(MomentMatchingLoss_th, self).__init__()
        self.moments = n_moments

    def forward(self, pred, target):
        """ Compute the loss model

        :param pred: predicted Variable
        :param target: Target Variable
        :return: Loss
        """

        loss = Variable(th.FloatTensor([0]))
        for i in range(1, self.moments):
            mk_pred = th.mean(th.pow(pred, i), 0)
            mk_tar = th.mean(th.pow(target, i), 0)

            loss.add_(th.mean((mk_pred - mk_tar) ** 2))  # L2

        return loss
