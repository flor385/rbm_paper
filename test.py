from rbm import RBM
import util
import logging
import numpy as np


log = logging.getLogger(__name__)


def main():
    logging.basicConfig(level=logging.INFO)
    log.info('Test cod for Theano DBN')

    rbm = RBM(32 * 24, 10)
    # util.display_RBM(rbm, 32, 24)

    #   trainset loading
    X, y, classes = util.load_trainset()
    log.info('Read %d samples', len(y))

    #   for testing use only classes A, B, C
    log.info('Taking a subset of training data')
    classes_mod = 'A'
    bool_mask = np.array([(classes[ind] in classes_mod) for ind in y])
    X_mod = X[bool_mask]
    log.info('Subset has %d elements', len(X_mod))

    #   train the RBM for a while!
    X_mnb = util.create_minibatches(X_mod, None, 20)

    cost, time, hist = rbm.train(
        X_mnb, 2, eps=0.05, spars=0.15, spars_cost=2.0, pcd=False, steps=20)

    np.set_printoptions(formatter={'float': '{: 0.3f}'.format})
    log.info('Cost: \n%r', cost)
    log.info('Time: \n%r', time)
    log.info('Hist: \n%r', hist)

    util.pickle_zip(rbm, 'testing.zip')
    util.unpickle_unzip('testing.zip')


if __name__ == '__main__':
    main()