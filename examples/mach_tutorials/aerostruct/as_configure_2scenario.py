#rst Imports
from __future__ import print_function, division
import numpy as np
from mpi4py import MPI

import openmdao.api as om

from omfsi.as_group import as_group

from baseclasses import *
from tacs import elements, constitutive, functions

# set these for convenience
comm = MPI.COMM_WORLD
rank = comm.rank

class Top(om.Group):

    def setup(self):
        # in the setup method, we add all components
        # we need the options to create the components, so we set them here

        ################################################################################
        # ADflow options
        ################################################################################
        aero_options = {
            # I/O Parameters
            'gridFile':'wing_vol.cgns',
            'outputDirectory':'.',
            'monitorvariables':['resrho','cl','cd'],
            'writeTecplotSurfaceSolution':False,
            'writevolumesolution':False,
            'writesurfacesolution':False,

            # Physics Parameters
            'equationType':'RANS',

            # Solver Parameters
            'smoother':'dadi',
            'CFL':1.5,
            'CFLCoarse':1.25,
            'MGCycle':'sg',
            'MGStartLevel':-1,
            'nCyclesCoarse':250,

            # ANK Solver Parameters
            'useANKSolver':True,
            # 'ankswitchtol':1e-1,
            'nsubiterturb': 5,

            # NK Solver Parameters
            'useNKSolver':True,
            'nkswitchtol':1e-4,

            # Termination Criteria
            'L2Convergence':1e-14,
            'L2ConvergenceCoarse':1e-2,
            'nCycles':10000,

            # force integration
            'forcesAsTractions':False,
        }

        ADFLOW_builder = ADFLOW(aero_options) 

        ################################################################################
        # TACS options
        ################################################################################
        def add_elements(mesh):
            rho = 2780.0            # density, kg/m^3
            E = 73.1e9              # elastic modulus, Pa
            nu = 0.33               # poisson's ratio
            kcorr = 5.0 / 6.0       # shear correction factor
            ys = 324.0e6            # yield stress, Pa
            thickness= 0.020
            min_thickness = 0.002
            max_thickness = 0.05

            num_components = mesh.getNumComponents()
            for i in range(num_components):
                descript = mesh.getElementDescript(i)
                stiff = constitutive.isoFSDT(rho, E, nu, kcorr, ys, thickness, i,
                                            min_thickness, max_thickness)
                element = None
                if descript in ["CQUAD", "CQUADR", "CQUAD4"]:
                    element = elements.MITCShell(2,stiff,component_num=i)
                mesh.setElement(i, element)

            ndof = 6
            ndv = num_components

            return ndof, ndv

        def get_funcs(tacs):
            ks_weight = 50.0
            return [ functions.KSFailure(tacs,ks_weight), functions.StructuralMass(tacs)]

        tacs_setup = {'add_elements': add_elements,
                    'mesh_file'   : 'wingbox.bdf',
                    'get_funcs'   : get_funcs}

        TACS_builder = TACS(tacs_setup) 

        ################################################################################
        # Transfer scheme options
        ################################################################################
        meld_options = {'isym': 2,
                        'n': 200,
                        'beta': 0.5}

        MELD_builder = MELD(meld_options)
        
        # add the aerostructural group
        #self.add_subsystem('mp_group', AS_Multipoint(aero_builder       = ADFLOW_builder,
        #                                             struct_builder     = TACS_builder,
        #                                             xfer_builder       = MELD_builder
        #                                             n_scenario       = 2))

        mp = self.add_subsystem('mp_group', AS_Multipoint(n_scenario=2))
        
        mp.mphy_add_scenario('s1', Scenario(aero_builder=ADflow_builder,
                                            struct_builder=TACS_builder,
                                            xfer_builder=MELD_builder),
                             min_procs=1,
                             max_procs=None,
        #                     promotes_inputs['dv_struct']
        )
        mp.mphy_add_scenario('s2', Scenario(aero_builder=VLM_builder, # could also be another ADflow builder with different options 
                                            struct_builder=TACS_builder,
                                            xfer_builder=MELD_builder),
                             min_procs=1,
                             max_procs=None,
        #                     promotes_inputs=['dv_struct']
        )

        dvs = self.mp.add_subsystem('dvs', om.IndepVarComp(), promotes=['*'])
        dvs.add_output('dv_struct', np.ones(1000)))

    def configure(self):


        # CREATE THE AEROPROBLEMS
        AP0 = AEROPROBLEM(NAME='WING0',
            MACH=0.8,
            ALTITUDE=10000,
            ALPHA=1.5,
            AREAREF=45.5,
            chordRef=3.25,
            evalFuncs=['lift','drag', 'cl', 'cd']
        )
        ap0.addDV('alpha',value=1.5,name='alpha')
        ap0.addDV('mach',value=0.8,name='mach')

        AP1 = AeroProblem(name='wing1',
            mach=0.7,
            altitude=10000,
            alpha=1.5,
            areaRef=45.5,
            chordRef=3.25,
            evalFuncs=['lift','drag', 'cl', 'cd']
        )
        ap1.addDV('alpha',value=1.5,name='alpha')
        ap1.addDV('mach',value=0.7,name='mach')

        # here we set the aero problems for every cruise case we have.
        # this can also be called set_flow_conditions, we don't need to create and pass an AP,
        # just flow conditions is probably a better general API
        # this call automatically adds the DVs for the respective scenario
        self.mp_group.s1.aero.set_ap(AP0)
        self.mp_group.s2.aero.set_ap(AP1) 

        # TODO: this is a structural dv that should be configured ina  more clean way...
        self.mp_group.add_dv_struct('dv_struct', np.array(self.as_group.n_dv_struct*[0.01]))

        self.mp_group.promote('s1', inputs=['dv_struct'])
        self.mp_group.promote('s2', inputs=['dv_struct'])

        # we can also add additional design variables, constraints and set the objective function here.
        # every solver is already initialized, so we can perform solver-specific calls
        # that are not in default OMFSI API.

################################################################################
# OpenMDAO setup
################################################################################
prob = om.Problem()
prob.model = Top()
model = prob.model
prob.setup()
om.n2(prob, show_browser=False, outfile='as_configure_2scenario.html')
prob.run_model()
# prob.model.list_outputs()
if MPI.COMM_WORLD.rank == 0:
    print("Scenario 0")
    print('cl =',prob['as_group.cruise0.aero_funcs.cl'])
    print('cd =',prob['as_group.cruise0.aero_funcs.cd'])

    print("Scenario 1")
    print('cl =',prob['as_group.cruise1.aero_funcs.cl'])
    print('cd =',prob['as_group.cruise1.aero_funcs.cd'])
