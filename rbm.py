"""
Implementation of a Restricted Boltzmann Machine.

Uses Theano, but does not utilize automatic gradient
calculation based on free energy (like in the Theano
RBM tutorial), but instead uses already defined CD
and PCD expressions.
"""
import numpy as np
import theano
import theano.tensor as T
import logging
from time import time

log = logging.getLogger(__name__)


class RBM():

    """A Restricted Boltzmann Machine"""

    def __init__(self, n_vis, n_hid, input=None,
                 W=None, b_vis=None, b_hid=None):
        """
        :param n_vis: Number of visible units

        :param n_hid: Number of hidden units

        :param input: None for standalone RBMs or symbolic variable if RBM is
        part of a larger graph.

        :param W: None for standalone RBMs or symbolic variable pointing to a
        shared weight matrix in case RBM is part of a DBN network; in a DBN,
        the weights are shared between RBMs and layers of a MLP

        :param hbias: None for standalone RBMs or symbolic variable pointing
        to a shared hidden units bias vector in case RBM is part of a
        different network

        :param vbias: None for standalone RBMs or a symbolic variable
        pointing to a shared visible units bias
        """
        log.info('Creating RBM, n_vis = %d, n_hid = %d', n_vis, n_hid)
        log.debug('Theano floatX set to %s', theano.config.floatX)

        self.n_vis = n_vis
        self.n_hid = n_hid

        #   random number generators
        numpy_rng = np.random.RandomState(1234)
        self.numpy_rng = numpy_rng
        self.theano_rng = T.shared_randomstreams.RandomStreams(
            numpy_rng.randint(2 ** 30))

        # initialize input layer for standalone RBM
        if input is None:
            input = T.matrix('input')
        self.input = input

        #   if weights are not provided, initialize them
        if W is None:
            initial_W = np.asarray(
                numpy_rng.uniform(
                    low=-4 * np.sqrt(6. / (n_hid + n_vis)),
                    high=4 * np.sqrt(6. / (n_hid + n_vis)),
                    size=(n_vis, n_hid)
                ),
                dtype=theano.config.floatX
            )
            W = theano.shared(value=initial_W, name='W', borrow=True)
        self.W = W

        #   if hidden biases are not provided, initialize them
        if b_hid is None:
            b_hid = theano.shared(
                value=np.zeros(n_hid, dtype=theano.config.floatX),
                name='b_hid',
                borrow=True
            )
        self.b_hid = b_hid

        #   if visible biases are not provided, initialize them
        if b_vis is None:
            b_vis = theano.shared(
                value=np.zeros(n_vis, dtype=theano.config.floatX),
                name='b_vis',
                borrow=True
            )
        self.b_vis = b_vis

    def hid_given_vis(self, vis):
        """
        Creates and returns Symbolic Theano variables for the
        probabilities and activations
        of the hidden layer, given the activations of the visible
        layer.

        :param vis: Symbolic variable for the visible layer. Expecting
            a matrix of (N, n_vis) shape.
        """

        if getattr(self, 'hid_given_vis_f', None) is None:
            
            vis_input = T.matrix('vis_input')
            hid_prb = T.nnet.sigmoid(T.dot(vis_input, self.W) + self.b_hid)
            hid_act = self.theano_rng.binomial(
                n=1, p=hid_prb, size=hid_prb.shape, dtype=theano.config.floatX)

            self.hid_given_vis_f = theano.function(
                [vis_input], [hid_prb, hid_act])

        return self.hid_given_vis_f(vis)

    def step_given_hid(self, hid):
        """
        Creates and returns symbolic Theano variables for
        visible and hidden layer reconstructions (probabilities
        and activations) given the state of the hidden layer.

        Essentially it performs a single Gibbs step.

        :param hid: Symbolic Theano variable for the
            hidden layer, a matrix of (N, n_hid) shape
            is expected.
        """

        #   calculate visible layer probabilities and activations
        vis_prb = T.nnet.sigmoid(T.dot(hid, self.W.T) + self.b_vis)
        vis_act = self.theano_rng.binomial(
            n=1, p=vis_prb, size=vis_prb.shape, dtype=theano.config.floatX)

        #   calculate hidden layer probabilities and activations
        hid_prb = T.nnet.sigmoid(T.dot(vis_act, self.W) + self.b_hid)
        hid_act = self.theano_rng.binomial(
            n=1, p=hid_prb, size=hid_prb.shape, dtype=theano.config.floatX)

        #   return everything calculated
        return (vis_prb, vis_act, hid_prb, hid_act)

    def train(self, X_mnb, epochs, eps,
              pcd=False, steps=1, spars=None, spars_cost=None,
              weight_cost=1e-4):
        """
        Trains the RBM with the given data. Returns a tuple containing
        (costs, times, hid_unit_activation_histograms). All three
        elements are lists, one item per epoch except for 'times' that
        has an extra element (training start time).

        :param X_mnb: Trainset split into minibatches. Thus,
            X_mnb is an iterable containing numpy arrays of
            (mnb_N, n_vis) shape, where mnb_N is the number of
            samples in the minibatch.

        :param epochs: Number of epochs (int) of training.

        :param eps: Learning rate. Either a float value to be
            used directly, or a callable that determines the
            learning rate based on epoch number and a list of
            error rates.

        :param pcd: Boolean indicator if Persistent Contrastive Divergence
            should be used. If False, then plain CD is used instead of PCD.

        :param steps: The number of steps to be used in PCD / CD.
            Integer or callable, or a callable that determines the
            learning rate based on epoch number and a list of
            error rates.

        :param spars: Target sparsity of hidden unit activation.

        :param spars_cost: Cost of deviation from the target sparsity.

        :param weight_cost: Regularization cost for L2 regularization
            (weight decay).

        """

        log.info('Training RBM, epochs: %d, spars:%r', epochs, spars)

        #   hidden unit activation compiled function
        hid_given_vis_f = theano.function(
            [self.input], self.hid_given_vis(self.input))

        #   Gibbs sampling steps compiled function
        #   first we need some Theano variables for input
        #   symbolic variable for step input (hidden layer state)
        hid_input = T.matrix('hid_input', dtype=theano.config.floatX)
        n_steps = T.scalar('n_steps', dtype='int8')
        #   then we prepare the scan function
        (vis_prb, vis_act, hid_prb, hid_act), updates = theano.scan(
            self.step_given_hid,
            outputs_info=[None, None, None, hid_input],
            n_steps=n_steps)
        #   finally we compile the scan function
        steps_given_hid_f = theano.function(
            [hid_input, n_steps],
            [vis_prb, vis_act, hid_prb, hid_act],
            updates=updates)

        #   compile the cost function for PCD, if we use it
        if pcd:
            cost, updates = self.get_pseudo_likelihood_cost(self.input)
            pl_cost_f = theano.function(
                [self.input],
                cost,
                updates=updates)

        #   initialize the vis biases according to the data
        b_vis_init = sum(
            [batch.mean(axis=0, dtype=theano.config.floatX)
             for batch in X_mnb],
            np.zeros(self.n_vis, dtype=theano.config.floatX)
        ) / len(X_mnb)
        b_vis_init = b_vis_init * 0.99     # avoid div with 0
        b_vis_init = np.log(b_vis_init / (1 - b_vis_init))
        self.b_vis.set_value(b_vis_init)

        #   things we'll track through training, for reporting
        epoch_costs = []
        epoch_times = [time()]
        epoch_hid_prbs = np.zeros((epochs, self.n_hid))

        #   if using PCD, we need the "fantasy particles"
        if pcd:
            _, neg_start = hid_given_vis_f(X_mnb[0])

        #   iterate through the epochs
        for epoch_ind, epoch in enumerate(range(epochs)):
            log.info('Starting epoch %d', epoch)

            #   iterate through the minibatches
            batch_costs = []
            for batch_ind, batch in enumerate(X_mnb):

                #   positive statistics
                #   _prb suffix indicates activation probabilities
                #   _act suffix indicates binary activation
                pos_hid_prb, pos_hid_act = hid_given_vis_f(batch)

                #   negative statistics Gibbs sampling start
                #   depends on whether we are using Persistent CD
                if pcd:
                    neg_start = neg_start
                else:
                    neg_start = pos_hid_act

                #   the number of Gibbs sampling steps
                if isinstance(steps, int):
                    n_steps = steps
                else:
                    n_steps = steps(epoch, epoch_costs)

                #   do Gibbs sampling
                neg_vis_prb, _, neg_hid_prb, neg_hid_act = steps_given_hid_f(
                    neg_start, n_steps)
                #   the scan function returns all steps
                #   we don't need them all
                neg_hid_act = neg_hid_act[-1]
                neg_vis_prb = neg_vis_prb[-1]
                neg_hid_prb = neg_hid_prb[-1]

                #   for PCD then next sampling step continues
                #   where the current one stopped
                if pcd:
                    neg_start = neg_hid_act

                #   gradients based on pos/neg statistics
                pos_vis = batch.mean(axis=0, dtype=theano.config.floatX)
                pos_hid = pos_hid_prb.mean(axis=0)
                grad_b_vis = pos_vis - neg_vis_prb.mean(axis=0)
                grad_b_hid = pos_hid - neg_hid_prb.mean(axis=0)
                grad_W = (np.dot(batch.T, pos_hid_prb) - np.dot(
                    neg_vis_prb.T, neg_hid_prb)) / len(batch)

                #   L2 regularization gradient
                # grad_b_vis += weight_cost * self.b_vis.get_value()
                grad_b_hid += weight_cost * self.b_hid.get_value()
                grad_W += weight_cost * self.W.get_value()

                #   sparsity gradient
                if((spars is not None) & (spars_cost is not None)):
                    spars_grad = (pos_hid - spars) * spars_cost * eps
                    grad_W -= np.dot(pos_vis.reshape((self.n_vis, 1)),
                                     spars_grad.reshape((1, self.n_hid)))
                    grad_b_hid -= spars_grad

                #   calc cost to be reported
                if pcd:
                    batch_costs.append(pl_cost_f(batch))
                else:
                    batch_costs.append(((neg_vis_prb - batch) ** 2).mean())

                #   hidden unit activation probability reporting
                #   note that batch.shape[0] is the number of samples in batch
                epoch_hid_prbs[epoch_ind, :] += pos_hid / batch.shape[0]

                #   updating the params
                self.W.set_value(self.W.get_value() + eps * grad_W)
                self.b_vis.set_value(self.b_vis.get_value() + eps * grad_b_vis)
                self.b_hid.set_value(self.b_hid.get_value() + eps * grad_b_hid)

            epoch_costs.append(np.array(batch_costs).mean())
            epoch_times.append(time())
            log.info(
                'Epoch cost %.5f, duration %.2f sec',
                epoch_costs[-1],
                epoch_times[-1] - epoch_times[-2]
            )

        log.info('Training duration %.2f min',
                 (epoch_times[-1] - epoch_times[0]) / 60.0)

        return epoch_costs, epoch_times, epoch_hid_prbs

    def free_energy(self, vis_act):

        wx_b = T.dot(vis_act, self.W) + self.b_hid
        vbias_term = T.dot(vis_act, self.b_vis)
        hidden_term = T.sum(T.log(1 + T.exp(wx_b)), axis=1)
        return -hidden_term - vbias_term

    def get_pseudo_likelihood_cost(self, vis_act):
        """Stochastic approximation to the pseudo-likelihood"""

        # index of bit i in expression p(x_i | x_{\i})
        bit_i_idx = theano.shared(value=0, name='bit_i_idx')

        # calculate free energy for the given bit configuration
        free_energy = self.free_energy(vis_act)

        # flip bit x_i of matrix vis_act and preserve all other bits x_{\i}
        # equal to vis_act[:,bit_i_idx] = 1-vis_act[:, bit_i_idx], but assigns
        # the result to vis_act_flip, instead of working in place on vis_act.
        vis_act_flip = T.set_subtensor(vis_act[:, bit_i_idx],
                                       1 - vis_act[:, bit_i_idx])

        # calculate free energy with bit flipped
        free_energy_flip = self.free_energy(vis_act_flip)

        # equivalent to e^(-FE(x_i)) / (e^(-FE(x_i)) + e^(-FE(x_{\i})))
        cost = T.mean(self.n_vis * T.log(T.nnet.sigmoid(free_energy_flip -
                                                        free_energy)))

        # increment bit_i_idx % number as part of updates
        updates = {bit_i_idx: (bit_i_idx + 1) % self.n_vis}

        return cost, updates

    def __getstate__(self):
        """
        We are overriding pickling to avoid pickling
        any CUDA stuff, that will make our pickles GPU
        dependent.
        """

        #   return what is normaly pickled
        W = self.W.get_value()
        b_vis = self.b_vis.get_value()
        b_hid = self.b_hid.get_value()
        n_vis = self.n_vis
        n_hid = self.n_hid
        return (W, b_vis, b_hid, n_vis, n_hid)

    def __setstate__(self, state):
        """
        We are overriding pickling to avoid pickling
        any CUDA stuff, that will make our pickles GPU
        dependent.
        """
        W, b_vis, b_hid, n_vis, n_hid = state
        self.W = theano.shared(value=W, name='W', borrow=True)
        self.b_vis = theano.shared(value=b_vis, name='b_vis', borrow=True)
        self.b_hid = theano.shared(value=b_hid, name='b_hid', borrow=True)
        self.n_hid = n_hid
        self.n_vis = n_vis

        #   random number generators
        numpy_rng = np.random.RandomState(1234)
        self.numpy_rng = numpy_rng
        self.theano_rng = T.shared_randomstreams.RandomStreams(
            numpy_rng.randint(2 ** 30))

        # initialize input layer for standalone RBM
        self.input = T.matrix('input')