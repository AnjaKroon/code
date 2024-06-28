import torch
import torch_geometric as pyg
import functorch


class GCNN(torch.nn.Module):
    def __init__(self, n_nodes, n_features, n_output_features, device, dtype, fixed_edge_weights) -> None:
        super(GCNN, self).__init__()
        self.n_nodes = n_nodes
        self.n_features = n_features
        self.n_output_features = n_output_features
        self.device = device
        self.dtype = dtype
        self.fixed_edge_weights = fixed_edge_weights
        
        self.graph_convolution = pyg.nn.GCNConv(n_features, n_output_features)
        
    def forward(self, x, edge_idx, edge_weights=None):
        x_out = torch.zeros(x.shape[0], self.n_nodes, self.n_output_features, device= self.device, dtype= self.dtype)
        
        for i in range(x.shape[0]):            
            x_out[i, :, :] = self.graph_convolution(x[i, :, :], edge_idx[i, :, :], edge_weights[i, :])
        return x_out
"""
needs to be imported like this:
    from <path> import <model_name>
Instantiated like this:
    model = <model_name>(num_params, input_horizon, prediction_horizon, other....) #model init
The used like this:
    prediction = model(input_data) #forward pass
"""

class GCNN_RNN(torch.nn.Module):
    def __init__(self, input_horizon, prediction_horizon, n_nodes, n_features, n_out_features, h_size, device, dtype, fixed_edge_weights, mlp_width):
        super(GCNN_RNN, self).__init__()
        self.device = device
        self.dtype = dtype
        self.n_nodes = n_nodes
        self.n_features = n_features
        self.h_size = h_size
        self.n_out_features = n_out_features
        self.input_horizon = input_horizon
        self.fixed_edge_weights = fixed_edge_weights.to(device)
        self.mlp_width = mlp_width
        #currently not using these
        self.prediction_horizon = prediction_horizon
        
        if dtype != torch.float32:
            raise ValueError("Only float32 is supported")
        self.GCNN = GCNN(n_nodes= n_nodes, n_features= n_features, 
                         n_output_features= n_out_features, device= device, 
                         dtype= dtype, fixed_edge_weights= fixed_edge_weights)
        self.GCNN.to(device)
        self.RNN = torch.nn.RNN(input_size=n_out_features, hidden_size=h_size,  batch_first=True, device=device, dtype=dtype)
        self.MLP = torch.nn.Sequential(
            torch.nn.Linear(h_size, h_size*mlp_width),
            torch.nn.ReLU(),
            torch.nn.Linear(h_size*mlp_width, h_size*mlp_width),
            torch.nn.ReLU(),
            torch.nn.Linear(h_size*mlp_width, n_out_features)
            
        )
    def forward(self, x_in, edge_weights=None, pred_hor = 1):
        if edge_weights is not None:
            raise ValueError("Only fixed edge weights are supported")
        if self.fixed_edge_weights is None:
            raise ValueError("Fixed edge weights must be provided")
        batch_size = x_in.shape[0]
        
        x_in = x_in.view(-1, self.n_nodes, self.n_features)
        x_in.to(self.device)
        
        fixed_edge_weights = self.fixed_edge_weights.transpose(0, 1)
        
        self.dup_fixed_edge_weights = fixed_edge_weights.unsqueeze(0).expand(batch_size*self.input_horizon, -1, -1)
        self.dup_fixed_edge_idx = self.dup_fixed_edge_weights[:, :2, :].type(torch.int32)
        self.dup_fixed_edge_weights = self.dup_fixed_edge_weights[:, 2, :]
        self.dup_fixed_edge_idx.to(self.device)
        self.dup_fixed_edge_weights.to(self.device)
        
        # Extracting node IDs and creating a mapping from IDs to indices
        unique_id = self.dup_fixed_edge_idx[:,:,:].unique()
        map_id = {j.item(): i for i, j in enumerate(unique_id)}

        # Processing edge Tensor: replacing node IDs with corresponding indices
        for i, batch in enumerate(self.dup_fixed_edge_idx):
            for j, _ in enumerate(batch[0]):
                self.dup_fixed_edge_idx[i, 0, j] = map_id[self.dup_fixed_edge_idx[i, 0, j].item()]
                self.dup_fixed_edge_idx[i, 1, j] = map_id[self.dup_fixed_edge_idx[i, 1, j].item()]
        
        x = self.GCNN(x_in, edge_idx = self.dup_fixed_edge_idx, edge_weights= self.dup_fixed_edge_weights)
        
        # I reduce the input horizon by 2, because otherwise the size of x doesn't factor out to these four variables
        x = x.view(batch_size * self.n_nodes, self.input_horizon, self.n_out_features)
        
        #change 1 to n_layers
        h = torch.zeros((1, batch_size * self.n_nodes, self.h_size), device=self.device)
        
        print(" X shape: ", x.shape)
        print("H shape: ", h.shape)
        
        x_out_final = torch.zeros((batch_size * self.n_nodes, self.input_horizon + self.prediction_horizon, self.n_out_features), device=self.device)
        RNN_out, h_out = self.RNN(x, h)
        print("x_out.shape) ", RNN_out.shape)
        print("h_out.shape) ", h_out.shape)
        
        for i in range(self.input_horizon):
            x_out_final[:, i, :] = self.MLP(RNN_out[:, i, :])
            
        if pred_hor > 1:
            for i in range(pred_hor-1):
                RNN_out, h_out = self.RNN(x_out_final[:,self.input_horizon+i, :], h_out[-1, :, :])
                
                x_out_final[:, self.input_horizon + i, :] = self.MLP(RNN_out[:, :, :])
        
        print(x_out_final.shape)        
        print(x_out_final[0, :, :5])
        x_out_final = x_out_final.view(batch_size, self.input_horizon + self.prediction_horizon, self.n_nodes, self.n_out_features)
        print(x_out_final.shape)
        return x_out_final
    
