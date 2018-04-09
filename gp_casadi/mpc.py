# -*- coding: utf-8 -*-
"""
Gaussian Process
@author: Helge-André Langåker
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from sys import path
path.append(r"C:\Users\helgeanl\Google Drive\NTNU\Masteroppgave\casadi-py36-v3.4.0")
import time
import numpy as np
import matplotlib.pyplot as plt
import casadi as ca
import casadi.tools as ctools
from matplotlib.font_manager import FontProperties
from simulation.four_tank import sim_system
from . gp_functions import gp_taylor_approx, gp


def cost_lf(x, x_ref, covar_x, P, s=2):
    # Cost function
    P_s = ca.SX.sym('Q', ca.MX.size(P))
    x_s = ca.SX.sym('x', ca.MX.size(x))
    covar_x_s = ca.SX.sym('K', ca.MX.size(covar_x))
    
    sqnorm_x = ca.Function('sqnorm_x', [x_s, P_s],
                           [ca.mtimes(x_s.T, ca.mtimes(P_s, x_s))])
    trace_x = ca.Function('trace_x', [P_s, covar_x_s],
                           [s * ca.trace(ca.mtimes(P_s, covar_x_s))])
    return sqnorm_x(x - x_ref, P) + trace_x(P, covar_x) 

                 
def cost_l(x, x_ref, covar_x, u, Q, R, K, s=1):
    Q_s = ca.SX.sym('Q', ca.MX.size(Q))
    R_s = ca.SX.sym('R', ca.MX.size(R))
    K_s = ca.SX.sym('K', ca.MX.size(K))
    x_s = ca.SX.sym('x', ca.MX.size(x))
    u_s = ca.SX.sym('u', ca.MX.size(u))
    covar_x_s = ca.SX.sym('K', ca.MX.size(covar_x))
    covar_u_s = ca.SX.sym('covar_u', ca.MX.size(R))

    sqnorm_x = ca.Function('sqnorm_x', [x_s, Q_s],
                           [ca.mtimes(x_s.T, ca.mtimes(Q_s, x_s))])
    sqnorm_u = ca.Function('sqnorm_u', [u_s, R_s],
                           [ca.mtimes(u_s.T, ca.mtimes(R_s, u_s))])
    covar_u  = ca.Function('covar_u', [covar_x_s, K_s],
                           [ca.mtimes(K_s, ca.mtimes(covar_x_s, K_s.T))])
    trace_u  = ca.Function('trace_u', [R_s, covar_u_s],
                           [s * ca.trace(ca.mtimes(R_s, covar_u_s))])
    trace_x  = ca.Function('trace_x', [Q_s, covar_x_s],
                           [s * ca.trace(ca.mtimes(Q_s, covar_x_s))])  

    return sqnorm_x(x - x_ref, Q) + sqnorm_u(u, R) + trace_x(Q, covar_x) \
                 + trace_u(R, covar_u(covar_x, K))


def mpc(X, Y, x0, x_sp, invK, hyper, horizon, sim_time, dt, 
        ulb=None, uub=None, xlb=None, xub=None, terminal_constraint = None,
        feedback=False, method='TA', plot=False, meanFunc='zero'):
    """ Model Predictive Control
    
    # Arguments:
        X: Training data matrix with inputs of size (N x Nx), where Nx is the number
            of inputs to the GP and N number of training points.
        Y: Training data matrix with outpyts of size (N x Ny), with Ny number of outputs.
        invK: Array with the inverse covariance matrices of size (Ny x N x N), 
            with Ny number of outputs from the GP.
        hyper: Array with hyperparameters [ell_1 .. ell_Nx sf sn].
        method: Method of propagating the uncertainty
            Possible options:
                'TA': Second order taylor approximation
                'ME': Mean equivalent approximation without propagation 
    # Returns:
        mean: Simulated output
        u: Control inputs 
    """
    
    Nsim = int(sim_time / dt)

    Nt = int(horizon / dt)
    Ny = len(invK)
    Nx = X.shape[1]
    Nu = Nx - Ny
    
    P = np.eye(Ny) * 1
#    P = np.array([[6, .0, .0, .0], 
#                  [.0, 6, .0, .0],
#                  [.0, .0, 6, .0],
#                  [.0, .0, .0, 31]])
#    Q = np.array([[6, .0, .0, .0], 
#                  [.0, 6, .0, .0],
#                  [.0, .0, 6, .0],
#                  [.0, .0, .0, 31]])
    Q = np.eye(Ny) * 1
    R = np.eye(Nu) * 0.01
    #K = np.zeros((Nu, Ny)) # * .5
    K = np.array([[1.8, .0, .5, .0], 
                  [.0, 1.8, .0, .5]]) * 0.0
    
    # Initial state
    mean_0 = x0 #np.array([8., 10., 8., 18.])
    mean_ref = x_sp #np.array([14., 14., 14.2, 21.3])
    variance_0 = np.ones(Ny) * 1e-5 * np.std(Y)
    
    mean_s = ca.MX.sym('mean', Ny)
    variance_s = ca.MX.sym('var', Ny)
    covar_x_s = ca.MX.sym('covar', Ny, Ny)
    v_s = ca.MX.sym('v', Nu)
    z_s = ca.vertcat(mean_s, v_s)
    
    if method is 'ME':
        gp_func = ca.Function('gp_mean', [z_s, variance_s], 
                            gp(invK, ca.MX(X), ca.MX(Y), ca.MX(hyper), 
                               z_s.T, meanFunc=meanFunc))
    elif method is 'TA':
        gp_func = ca.Function('gp_taylor_approx', [z_s, variance_s],
                            gp_taylor_approx(invK, ca.MX(X), ca.MX(Y), 
                                             ca.MX(hyper), z_s.T, variance_s, 
                                             meanFunc=meanFunc, diag=True))

    # Define stage cost and terminal cost
    l_func = ca.Function('l', [mean_s, covar_x_s, v_s], 
                           [cost_l(mean_s, ca.MX(mean_ref), covar_x_s, v_s, 
                                   ca.MX(Q), ca.MX(R), ca.MX(K))])
    lf_func = ca.Function('lf', [mean_s, covar_x_s], 
                           [cost_lf(mean_s, ca.MX(mean_ref), covar_x_s,  ca.MX(P))])
    # Feedback function
    if feedback:
        u_func = ca.Function('u', [mean_s, v_s], [ca.mtimes(ca.MX(K),
                             mean_s - ca.MX(mean_ref)) + v_s])
    else:
        u_func = ca.Function('u', [mean_s, v_s], [v_s])
    # Create variables struct
    var = ctools.struct_symMX([(
            ctools.entry('mean', shape=(Ny,), repeat=Nt + 1),
            ctools.entry('variance', shape=(Ny,), repeat=Nt + 1),
            ctools.entry('v', shape=(Nu,), repeat=Nt)
    )])
    
    varlb = var(-np.inf)
    varub = var(np.inf)
    varguess = var(0)
    
    # Adjust the relevant constraints
    for t in range(Nt):
        if ulb is not None:
            varlb['v', t, :] = ulb
            if feedback:
                varlb['v', t, :] = ulb - np.dot(K, xub)
        if uub is not None:
            varub['v', t, :] = uub
            if feedback:
                varub['v', t, :] = uub - np.dot(K, xlb)
        if xlb is not None:
            varlb['mean', t, :] = xlb
        if xub is not None:
            varub['mean', t, :] = xub

    # Now build up constraints and objective
    obj = ca.MX(0)
    con_mean = []
    con_var = []
    for t in range(Nt):
        u_i = u_func(var['mean', t], var['v', t])
        z = ca.vertcat(var['mean', t], u_i)
        mean_i, var_i = gp_func(z, var['variance', t])

        con_mean.append(var['mean', t + 1] - mean_i)
        con_var.append(var['variance', t + 1] - var_i)

        obj += l_func(var['mean', t], ca.diag(var['variance', t]), u_i)
    obj += lf_func(var['mean', Nt], ca.diag(var['variance', Nt]))
    
    if terminal_constraint is not None:
        con_mean.append(var['mean', Nt] - mean_ref)
        conlb = np.zeros((Ny * Nt * 2 + Ny,))
        conub = np.zeros((Ny * Nt * 2 + Ny,))
        conlb[Ny * t] = - terminal_constraint
        conub[Ny * t] = terminal_constraint
    else:
        conlb = np.zeros((Ny * Nt * 2,))
        conub = np.zeros((Ny * Nt * 2,))
    con = ca.vertcat(*con_mean, *con_var)
    # Build solver object    
    nlp = dict(x=var, f=obj, g=con)
    opts = {}
    opts['ipopt.print_level'] = 0
    opts['ipopt.linear_solver'] = 'ma27'
    opts['ipopt.max_cpu_time'] = 1
    opts['print_time'] = False
    opts['expand'] = True
    solver = ca.nlpsol('solver', 'ipopt', nlp, opts)

    # Simulate
    mean = np.zeros((Nsim + 1, Ny))
    mean[0, :] = mean_0
    variance = np.zeros((Nsim + 1, Ny))
    variance[0, :] = variance_0
    u = np.zeros((Nsim, Nu))
    
    print('\nSolving MPC with %d step horizon' % Nt)
    for t in range(Nsim):
        solve_time = -time.time()

        # Fix initial state
        varlb['mean', 0, :] = mean[t, :]
        varub['mean', 0, :] = mean[t, :]
        varguess['mean', 0, :] = mean[t, :]
        varlb['variance', 0, :] = variance[t, :]
        varub['variance', 0, :] = variance[t, :]
        varguess['variance', 0, :] = variance[t, :]
        args = dict(x0=varguess,
                    lbx=varlb,
                    ubx=varub,
                    lbg=conlb,
                    ubg=conub)

        # Solve nlp
        sol = solver(**args)
        status = solver.stats()['return_status']
        optvar = var(sol['x'])
        solve_time += time.time()

        if t == 0:
            var_prediction = np.zeros((Nt + 1, Ny))
            mean_prediction = np.zeros((Nt + 1, Ny))
            for i in range(Nt + 1):
                var_prediction[i, :] = np.array(optvar['variance', i, :]).flatten()
                mean_prediction[i, :] = np.array(optvar['mean', i, :]).flatten()

        # Print status
        print("* t=%d: %s - %f s" % (t * dt, status, solve_time))
        v = optvar['v', 0, :]
        u[t, :] = np.array(u_func(mean[t, :], v)).flatten()
        variance[t + 1, :] = np.array(optvar['variance', -1, :]).flatten()
        try:
            mean[t + 1, :] = sim_system(mean[t, :], u[t, :].reshape((1, 2)), 
                                dt, dt, noise=True)
        except RuntimeError:
            print('********************************')
            print('* Runtime error, adding jitter *')
            print('********************************')
            if np.any(u < 1e-6):
                u = u +  1e-2 # Add jitter
            if np.any(mean < 1-1e6):
                mean = mean +  1e-3
            print(mean)
            print(u)
            mean[t + 1, :] = sim_system(mean[t, :], u[t, :].reshape((1, 2)), 
                                dt, dt, noise=True)

    if plot:
        x_sp = np.ones((Nsim + 1, Ny)) * mean_ref
        fig_x, fig_u = plot_mpc(x=mean, u=u, dt=dt, x_pred=mean_prediction, 
                           var_pred=var_prediction, x_sp=x_sp,
                           xnames=['Tank %d [cm]' % (i + 1) for i in range(Nx)],
                           unames=['Flow input %d [ml/s]' % (i + 1) for i in range(Nx)],
                           title='MPC with %d step/ %d s horizon - GP: %s' % (Nt, horizon, method)
                       )
        fig_x.savefig("mpc.png", bbox_inches="tight")

    return mean, u


def plot_mpc(x, u, dt, x_pred=None, var_pred=None, x_sp=None, title=None, 
             xnames=None, unames=None, time_unit = 's', numcols=2):
    Nu = np.size(u, 1)
    Nt_sim, Nx = x.shape
    Nt_horizon = np.size(x_pred, 0)
    if xnames is None:
        xnames = ['State %d' % (i + 1) for i in range(Nx)]
    if unames is None:
        unames = ['Control %d' % (i + 1) for i in range(Nu)]
    
    t = np.linspace(0.0, Nt_sim * dt, Nt_sim)
    t_horizon = np.linspace(0.0, Nt_horizon * dt, Nt_horizon)

    u = np.vstack((u, u[-1, :]))
    numcols = 2 
    numrows = int(np.ceil(Nx / numcols))

    fig_u = plt.figure() 
    for i in range(Nu):
        ax = fig_u.add_subplot(Nu, 1, i + 1)
        ax.step(t, u[:, i] , 'k', where='post')
        ax.set_ylabel(unames[i])
        ax.set_xlabel('Time [' + time_unit + ']')
    fig_u.canvas.set_window_title('Control inputs')

    fig_x = plt.figure() 
    for i in range(Nx):
        ax = fig_x.add_subplot(numrows, numcols, i + 1)
        ax.plot(t, x[:, i], 'b-', marker='.', linewidth=1.0, label='Simulation')
        if x_sp is not None:
            #ax.axhline(y=x_sp[i], color='g', linestyle='--', label='Setpoint')
            ax.plot(t, x_sp[:, i], color='g', linestyle='--', label='Setpoint')
        ax.errorbar(t_horizon, x_pred[:, i], yerr=2 * np.sqrt(var_pred[:, i]), 
                     linestyle='None', marker='.', color='r', label='1st prediction')
        #plt.plot(t2, mean_prediction[:, i], 'r.', label='1st prediction')
        #plt.gca().fill_between(t2.flat, mean_prediction[:, i] - 
        #       2 * np.sqrt(var_prediction[:, i]), mean_prediction[:, i] + 
        #       2 * np.sqrt(var_prediction[:, i]), color="#bbbbbb", label='95% conf prediction')
        plt.legend(loc='best')
        ax.set_ylabel(xnames[i])
        ax.set_xlabel('Time [' + time_unit + ']')
    #ax[1].legend(prop=fontP, bbox_to_anchor=(1.04,0.5), loc="center left", borderaxespad=0 )
    #ax[1].legend(prop=fontP, loc="best" )
    #plt.tight_layout(pad=1, rect=[0,0,0.75,1])
    #plt.tight_layout(pad=.1)
    if title is not None:
        fig_x.canvas.set_window_title(title)

    return fig_x, fig_u
