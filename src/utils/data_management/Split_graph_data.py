import os
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

import torch
import pandas as pd
from tqdm import tqdm
import numpy as np
import matplotlib.pyplot as plt
from src.utils.data_management.preprocessor import Preprocessor

class Split_graph_dataset(torch.utils.data.Dataset):
    def __init__(self, epi_dates, flow_dataset="data/daily_county2county_2019_01_01.csv", 
        epi_dataset="data_epi/epidemiology.csv", input_hor=4, pred_hor=1, fake_data=False, normalize_edge_weights=True):
        super(Split_graph_dataset, self).__init__()
        self.input_hor = input_hor
        self.pred_hor = pred_hor
        self.normalize_edge_weights = normalize_edge_weights
        if fake_data:
            self.generate_fake_data()
            return
        
        preprocessor = Preprocessor(flow_dataset, epi_dataset, epi_dates, plottable=True)
        flow_df, signals_df = preprocessor.get_data_for_graphRNN()
        print(f"Flow data shape: {flow_df.head()}")
        print(f"Signals[0] data shape: {signals_df[0].head()}")
    
        self.n_time = len(signals_df)

        self.prev_node_ids = signals_df[0]['geoid_o'].unique().tolist()
        self.prev_node_ids.sort()
        
        for i in range(self.n_time):
            self.node_ids =  signals_df[i]['geoid_o'].unique().tolist()
            self.node_ids.sort()
            if self.node_ids != self.prev_node_ids:
                raise ValueError(f"Node IDs do not match between time steps {i} and {i-1}. Time step {i-1} has {len(self.prev_node_ids)} nodes, time step {i} has {len(self.node_ids)} nodes")
            self.prev_node_ids = self.node_ids
            
        self.node_ids_from_edges = torch.cat((torch.tensor(flow_df['geoid_o'].unique()), torch.tensor(flow_df['geoid_d'].unique()))).unique().tolist()
        self.node_ids_from_edges.sort()
        if self.node_ids != self.node_ids_from_edges:
            raise ValueError(f"Node ID lists do not contain the same nodes.  Node IDs from edges: {self.node_ids_from_edges[:10]}, Node IDs from signals: {self.node_ids[:10]}. Sizes: {len(self.node_ids_from_edges)}, {len(self.node_ids)}")    
        self.n_nodes = len(self.node_ids)
        
        print(f"Number of time steps: {self.n_time}")
        print(f"Number of unique nodes with features: {len(self.node_ids)}")
        self.n_features = 1
        self.node_id2idx = {node_id: idx for idx, node_id in enumerate(self.node_ids)}
        
        self.edge_weights =  self.calc_edge_weights(flow_df)
        self.node_data = self.calc_node_data(signals_df) # (n_time, n_nodes, n_features)
        
        if normalize_edge_weights:
            mean_edge_weight = self.edge_weights[:, :, 2].mean().item()
            std_dev_edge_weight = self.edge_weights[:, :, 2].std().item()
            self.edge_weights[:, :, 2] = (self.edge_weights[:, :, 2] - mean_edge_weight) / std_dev_edge_weight
            min_edge_weight = self.edge_weights[:, :, 2].min().item()
            self.edge_weights[:, :, 2] = self.edge_weights[:, :, 2] - min_edge_weight
        else:
            max_edge_weight = self.edge_weights[:, :, 2].abs().max().item()
            self.edge_weights[:, :, 2] = self.edge_weights[:, :, 2] / max_edge_weight
            min_edge_weight = self.edge_weights[:, :, 2].min().item()
            self.edge_weights[:, :, 2] = self.edge_weights[:, :, 2] - min_edge_weight

        for feature in range(self.n_features):
            # mean_node_data = self.node_data[:, :, feature].mean().item()
            # std_dev_node_data = self.node_data[:, :, feature].std().item()
            # self.node_data[:, :, feature] = (self.node_data[:, :, feature] - mean_node_data) / std_dev_node_data
            max_feature_val = self.node_data[:, :, feature].max().item()
            min_feature_val = self.node_data[:, :, feature].min().item()
            self.node_data[:, :, feature] = (self.node_data[:, :, feature] - min_feature_val) / (max_feature_val - min_feature_val)
            
        
        check_data = True   
        if check_data:
            print(f"node_data: {self.node_data.shape}  {self.node_data.dtype} || {self.node_data[:, :, 0].mean().item()} {self.node_data[:, :, 0].std().item()}")
            print(f"edge_weights: {self.edge_weights.shape} {self.edge_weights.dtype} || {self.edge_weights[:, :, 2].mean().item()} {self.edge_weights[:, :, 2].std().item()}")
            print("=====================================")
            print(f"Node sample: {self.node_data[0, :5, 0]}")
            print(f"Edge sample: {self.edge_weights[0, :5, :]}")
            print("=====================================")
            self.node_ids_from_edges = torch.cat((self.edge_weights[:, :, 0].unique(), self.edge_weights[:, :, 1].unique())).unique().tolist()
            self.node_ids_from_edges.sort()
            self.node_ids.sort()
            print(f"Number of unique nodes from final edges: {len(self.node_ids_from_edges)}")
            print(f"Number of unique nodes from signals: {len(self.node_ids)}")
            
            if self.node_ids != self.node_ids_from_edges:
                num_equal_elements = len(set(self.node_ids) & set(self.node_ids_from_edges))
                print(f"Number of equal elements: {num_equal_elements}")
                print(f"Node IDs from edges: {self.node_ids_from_edges[num_equal_elements -10: num_equal_elements + 10]}")
                raise ValueError(f"Node ID lists do not contain the same nodes.  Node IDs from edges: {self.node_ids_from_edges[:10]}, Node IDs from signals: {self.node_ids[:10]}. Sizes: {len(self.node_ids_from_edges)}, {len(self.node_ids)}")
            print("Node IDs match between edges and signals")
            print("=====================================")
            print("Sparsity of node data: ", self.node_data.eq(0).sum().item() / self.node_data.numel())
            print("Sparsity of edge weights: ", self.edge_weights.eq(0).sum().item() / self.edge_weights.numel())
            
    def calc_edge_weights(self, flow_df):
        n_raw_edges = len(flow_df)
        edge_weights = torch.zeros((self.n_time, n_raw_edges, 3), dtype=torch.float32)
        
        self.n_edges = 0
        for j in tqdm(range(n_raw_edges)):
            pop_flow = flow_df.iloc[j]['pop_flows']
            
            origin = flow_df.iloc[j]['geoid_o']
            if origin not in self.node_ids:
                raise ValueError(f"Origin {origin} not in node_ids")
                continue
            
            destination = flow_df.iloc[j]['geoid_d']
            if destination not in self.node_ids:
                raise ValueError(f"Destination {destination} not in node_ids")
                continue

            edge_weights[0][self.n_edges ][0] = origin
            edge_weights[0][self.n_edges ][1] = destination
            edge_weights[0][self.n_edges ][2] = pop_flow
            self.n_edges += 1

        edge_weights = edge_weights[0].repeat(self.n_time, 1, 1)
        
        edge_weights = edge_weights.float()  
        
        return edge_weights
    
    def calc_node_data(self, signals_df):
        # Initialize node_data tensor
        node_data = torch.zeros((self.n_time, self.n_nodes, self.n_features), dtype=torch.float32)
        
        # Create a DataFrame to store all the data together
        all_data = pd.DataFrame()
        
        for t, df in enumerate(signals_df):
            df['time'] = t
            all_data = pd.concat([all_data, df[['time', 'geoid_o', 'new_confirmed']]], axis=0)
        
        # Create a multi-index DataFrame with all dates and node_ids
        index = pd.MultiIndex.from_product([range(self.n_time), self.node_ids], names=['time', 'geoid_o'])
        node_data_df = pd.DataFrame(0, index=index, columns=['new_confirmed'])
        
        # Set the index of the combined DataFrame
        all_data.set_index(['time', 'geoid_o'], inplace=True)
        
        # Update the multi-index DataFrame with the combined DataFrame
        node_data_df.update(all_data)
        
        # Unstack the DataFrame to convert it to the desired shape
        node_data_array = node_data_df.unstack(level='geoid_o').values
        
        # Expand dimensions and convert to tensor
        node_data_array = np.expand_dims(node_data_array, axis=2)
        node_data = torch.tensor(node_data_array, dtype=torch.float32)
        
        return node_data
    
    def __len__(self):
        return self.n_time - (self.input_hor + self.pred_hor) + 1
    
    def __getitem__(self, idx):
        edge_weights = self.edge_weights[idx:idx + self.input_hor + self.pred_hor]
        node_data = self.node_data[idx:idx + self.input_hor + self.pred_hor]
        
        input_edge_weights = edge_weights[:self.input_hor]
        input_node_data = node_data[:self.input_hor]
        target_edge_weights = edge_weights[self.input_hor:]
        target_node_data = node_data[self.input_hor:]
        
        if input_node_data.shape[0] != self.input_hor:
            raise ValueError(f"Input node data shape {input_node_data.shape} does not match input horizon {self.input_hor}")
        
        if target_node_data.shape[0] != self.pred_hor:
            raise ValueError(f"Target node data shape {target_node_data.shape} does not match prediction horizon {self.pred_hor}")
        
        return input_edge_weights, input_node_data, target_edge_weights, target_node_data
    
    def visualize(self, index, num_nodes=5, num_edges=5):
        if index >= len(self):
            raise ValueError(f"Index {index} out of bounds for dataset of length {len(self)}")
        
        input_edge_weights, input_node_data, target_edge_weights, target_node_data = self[index]
        
        fig, axes = plt.subplots(2, 1, figsize=(15, 10))

        # Plot node data
        axes[0].set_title("Node Data Over Time")

        for node in range(num_nodes):
            axes[0].plot(input_node_data[:, node, 0].numpy(), label=f"Node {node}")
        axes[0].legend()
        axes[0].set_xlabel("Time Step")
        axes[0].set_ylabel("Node Feature Value")
        
        # Plot edge weights
        axes[1].set_title("Edge Weights Over Time")
        for edge in range(num_edges):
            axes[1].plot(input_edge_weights[:, edge, 2].numpy(), label=f"Edge {edge}")
        axes[1].legend()
        axes[1].set_xlabel("Time Step")
        axes[1].set_ylabel("Edge Weight")

        plt.tight_layout()
        plt.show()

    def generate_fake_data(self):
        self.n_time = self.input_hor + self.pred_hor + 6
        self.n_nodes = 3000
        self.n_edges = 15000
        self.n_features = 1
        self.node_data = torch.randn((self.n_time, self.n_nodes, self.n_features), dtype=torch.float32)

        fake_idx = torch.randint(0, 10000, (self.n_nodes,))
        self.edge_weights = torch.zeros((self.n_time, self.n_edges, 3), dtype=torch.float32)

        for time in range(self.n_time):
            for node in range(self.n_edges):
                origin = torch.randint(0, self.n_nodes, (1,)).item()
                destination = torch.randint(0, self.n_nodes, (1,)).item()
                if time == 0:  
                    self.edge_weights[time, node, 0] = fake_idx[origin]
                    self.edge_weights[time, node, 1] = fake_idx[destination]
                    self.edge_weights[time, node, 2] = (torch.rand((1,)).item()-0.5)
                else:
                    self.edge_weights[time, node, 0] = fake_idx[origin]
                    self.edge_weights[time, node, 1] = fake_idx[destination]
                    self.edge_weights[time, node, 2] = self.edge_weights[time-1, node, 2] + 0.1*(torch.rand((1,)).item() - 0.5)
            for node in range(self.n_nodes):
                if time == 0:
                    self.node_data[time, node, 0] = torch.rand((1,)).item()-0.5
                else:
                    self.node_data[time, node, 0] = self.node_data[time-1, node, 0] + 0.1*(torch.rand((1,)).item() - 0.5)
        return



